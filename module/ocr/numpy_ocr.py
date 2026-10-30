"""
OCR backend for platforms without cnocr and mxnet, such as macOS on arm64.

Run the same pre-trained models in ./bin/cnocr_models on plain numpy.
Network is read from *-symbol.json and weights are read from *.params, with the same charset as AlOcr.
Pre-process and decoding are shared with AlOcr in AlOcrBase, the rest follows cnocr 1.2.
It aims at the same outputs as mxnet, see dev_tools/ocr_golden.py to compare with real mxnet results.
"""
import ast
import json
import os
import struct
from types import SimpleNamespace

import numpy as np
from PIL import Image

from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.al_ocr_base import AlOcrBase
from module.ocr.line_split import line_split

MXNET_LIST_MAGIC = 0x112
MXNET_NDARRAY_MAGIC = 0xF993FAC9
MXNET_DTYPE = {0: 'f4', 1: 'f8', 2: 'f2', 3: 'u1', 4: 'i4', 5: 'i1', 6: 'i8'}


def load_mxnet_params(file):
    """
    Read a .params file without mxnet.

    Args:
        file (str):

    Returns:
        dict[str, np.ndarray]: Key is parameter name, without the prefix `arg:` or `aux:`
    """
    with open(file, 'rb') as f:
        data = f.read()

    offset = 0

    def read(fmt):
        nonlocal offset
        value = struct.unpack_from('<' + fmt, data, offset)
        offset += struct.calcsize('<' + fmt)
        return value

    magic, _, count = read('QQQ')
    if magic != MXNET_LIST_MAGIC:
        raise ValueError(f'Not a mxnet params file: {file}')

    arrays = []
    for _ in range(count):
        magic, stype = read('Ii')
        if magic != MXNET_NDARRAY_MAGIC or stype != 0:
            raise ValueError(f'Unsupported ndarray in {file}, magic={magic}, stype={stype}')
        ndim, = read('I')
        shape = read(f'{ndim}q')
        read('ii')  # Device type and id
        dtype, = read('i')
        dtype = np.dtype('<' + MXNET_DTYPE[dtype])
        array = np.frombuffer(data, dtype=dtype, count=int(np.prod(shape)), offset=offset)
        offset += array.nbytes
        arrays.append(array.reshape(shape))

    count, = read('Q')
    keys = []
    for _ in range(count):
        length, = read('Q')
        keys.append(data[offset:offset + length].decode('utf-8'))
        offset += length

    return {key.split(':', 1)[-1]: array for key, array in zip(keys, arrays)}


def sliding_windows(x, kernel, stride):
    """
    Args:
        x (np.ndarray): Shape (N, C, H, W)
        kernel (tuple[int, int]):
        stride (tuple[int, int]):

    Returns:
        np.ndarray: Read only view, shape (N, C, new_H, new_W, kernel_H, kernel_W)
    """
    height, width = x.shape[2:]
    shape = x.shape[:2] + (
        (height - kernel[0]) // stride[0] + 1,
        (width - kernel[1]) // stride[1] + 1,
    ) + kernel
    strides = x.strides[:2] + (x.strides[2] * stride[0], x.strides[3] * stride[1]) + x.strides[2:]
    return np.lib.stride_tricks.as_strided(x, shape=shape, strides=strides, writeable=False)


def sigmoid(x):
    return 1 / (1 + np.exp(-np.clip(x, -50, 50)))


def gru_layer(x, wx, wh, bx, bh):
    """
    Same as mxnet GRUCell.

    Args:
        x (np.ndarray): Shape (T, N, input_size)
        wx (np.ndarray): Shape (3 * hidden, input_size)
        wh (np.ndarray): Shape (3 * hidden, hidden)
        bx (np.ndarray): Shape (3 * hidden,)
        bh (np.ndarray): Shape (3 * hidden,)

    Returns:
        np.ndarray: Shape (T, N, hidden)
    """
    hidden = wh.shape[1]
    if len(x) == 0:
        # Image is too narrow to have any time step
        return np.zeros((0, x.shape[1], hidden), dtype=x.dtype)
    state = np.zeros((x.shape[1], hidden), dtype=x.dtype)
    outputs = []
    for step in x:
        gate_x = step @ wx.T + bx
        gate_h = state @ wh.T + bh
        reset = sigmoid(gate_x[:, :hidden] + gate_h[:, :hidden])
        update = sigmoid(gate_x[:, hidden:2 * hidden] + gate_h[:, hidden:2 * hidden])
        new = np.tanh(gate_x[:, 2 * hidden:] + reset * gate_h[:, 2 * hidden:])
        state = (1 - update) * new + update * state
        outputs.append(state)
    return np.stack(outputs)


def op_convolution(attrs, x, weight):
    pad_h, pad_w = attrs['pad']
    x = np.pad(x, ((0, 0), (0, 0), (pad_h, pad_h), (pad_w, pad_w)), 'constant')
    kernel, stride = attrs['kernel'], attrs['stride']
    if kernel == (1, 1) and stride == (1, 1) and attrs['num_group'] == 1:
        return np.moveaxis(np.tensordot(x, weight[:, :, 0, 0], axes=(1, 1)), -1, 1)
    windows = sliding_windows(x, kernel, stride)
    if attrs['num_group'] > 1:
        if attrs['num_group'] != x.shape[1]:
            raise ValueError(f'Only depthwise group convolution is supported: {attrs}')
        return np.sum(windows * weight[:, 0][None, :, None, None, :, :], axis=(4, 5))
    return np.moveaxis(np.tensordot(windows, weight, axes=((1, 4, 5), (1, 2, 3))), -1, 1)


def op_batch_norm(attrs, x, gamma, beta, mean, var):
    shape = (1, -1, 1, 1)
    std = np.sqrt(var + attrs['eps'])
    return (x - mean.reshape(shape)) / std.reshape(shape) * gamma.reshape(shape) + beta.reshape(shape)


def op_pooling(attrs, x):
    return sliding_windows(x, attrs['kernel'], attrs['stride']).max(axis=(4, 5))


def op_reshape(attrs, x):
    shape = attrs['shape']
    if isinstance(shape, int):
        return x.reshape(shape)
    # 0 to copy a dimension, -3 to merge two, -2 to copy the rest
    new, axis = [], 0
    for code in shape:
        if code == 0:
            new.append(x.shape[axis])
            axis += 1
        elif code == -3:
            new.append(x.shape[axis] * x.shape[axis + 1])
            axis += 2
        elif code == -2:
            new.extend(x.shape[axis:])
        else:
            raise ValueError(f'Unsupported reshape: {shape}')
    return x.reshape(new)


def op_rnn(attrs, x, params, _):
    hidden = attrs['state_size']
    size = x.shape[2]
    # Order in _rnn_param_concat: weights of both directions, then biases of both directions
    weight_x, weight_h = 3 * hidden * size, 3 * hidden * hidden
    sizes = [weight_x, weight_h, weight_x, weight_h, 3 * hidden, 3 * hidden, 3 * hidden]
    wx_l, wh_l, wx_r, wh_r, bx_l, bh_l, bx_r, bh_r = np.split(params, np.cumsum(sizes))

    def run(sequence, wx, wh, bx, bh):
        return gru_layer(sequence, wx.reshape(3 * hidden, size), wh.reshape(3 * hidden, hidden), bx, bh)

    return np.concatenate([run(x, wx_l, wh_l, bx_l, bh_l), run(x[::-1], wx_r, wh_r, bx_r, bh_r)[::-1]], axis=2)


def op_softmax(_, x):
    x = np.exp(x - np.max(x, axis=1, keepdims=True))
    return x / np.sum(x, axis=1, keepdims=True)


OPERATORS = {
    'Convolution': op_convolution,
    'BatchNorm': op_batch_norm,
    'Activation': lambda attrs, x: np.maximum(x, 0),
    'Concat': lambda attrs, *x: np.concatenate(x, axis=attrs['dim']),
    'Pooling': op_pooling,
    'Reshape': op_reshape,
    'expand_dims': lambda attrs, x: np.expand_dims(x, attrs['axis']),
    'squeeze': lambda attrs, x: np.squeeze(x, attrs['axis']),
    'transpose': lambda attrs, x: np.transpose(x, attrs['axes']),
    'Dropout': lambda attrs, x: x,
    '_rnn_param_concat': lambda attrs, *x: np.concatenate(x, axis=attrs['dim']),
    '_zeros': lambda attrs: None,
    'RNN': op_rnn,
    'FullyConnected': lambda attrs, x, weight, bias: x @ weight.T + bias,
    'SoftmaxActivation': op_softmax,
}


# Attributes that operators above rely on
REQUIRED_ATTRS = {
    'Activation': {'act_type': 'relu'},
    'BatchNorm': {'axis': 1, 'fix_gamma': False},
    'Convolution': {'dilate': (1, 1), 'layout': 'NCHW', 'no_bias': True},
    'Pooling': {'global_pool': False, 'layout': 'NCHW', 'pad': (0, 0), 'pool_type': 'max',
                'pooling_convention': 'valid'},
    'RNN': {'bidirectional': True, 'mode': 'gru', 'num_layers': 1},
}


def check_node(op, attrs):
    """
    Reject what is not implemented here, instead of giving wrong results silently.

    Args:
        op (str):
        attrs (dict):
    """
    if op == 'null':
        return
    if op not in OPERATORS:
        raise ValueError(f'Unsupported operator: {op}')
    for key, value in REQUIRED_ATTRS.get(op, {}).items():
        if attrs.get(key) != value:
            raise ValueError(f'Unsupported attribute of {op}: {key}={attrs.get(key)}')


def parse_attrs(attrs):
    """
    Args:
        attrs (dict[str, str]):

    Returns:
        dict:
    """
    out = {}
    for key, value in attrs.items():
        try:
            out[key] = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            out[key] = value
    return out


class NumpyNetwork:
    def __init__(self, symbol_file, params_file):
        """
        Args:
            symbol_file (str):
            params_file (str):
        """
        with open(symbol_file, encoding='utf-8') as f:
            nodes = json.load(f)['nodes']
        # Layers behind softmax are for training
        end = [node['op'] for node in nodes].index('SoftmaxActivation')
        self.nodes = nodes[:end + 1]
        self.params = load_mxnet_params(params_file)
        self.attrs = [parse_attrs(node.get('attrs', {})) for node in self.nodes]
        for node, attrs in zip(self.nodes, self.attrs):
            check_node(node['op'], attrs)
        self.constants, self.steps = self.compile()

    def compile(self):
        """
        Evaluate nodes that do not depend on the input image, and plan the rest.

        Returns:
            list: Output of each node, None if it has to be computed in forward()
            list[tuple]: (index, operator, attrs, input indexes, indexes to release after this step)
        """
        last_use = {}
        for index, node in enumerate(self.nodes):
            for source in node['inputs']:
                last_use[source[0]] = index

        constants = [None] * len(self.nodes)
        known = set()
        steps = []
        for index, (node, attrs) in enumerate(zip(self.nodes, self.attrs)):
            sources = [source[0] for source in node['inputs']]
            if node['op'] == 'null':
                if node['name'] == 'data':
                    self.data_index = index
                else:
                    constants[index] = self.params[node['name']]
                    known.add(index)
            elif all(source in known for source in sources):
                constants[index] = OPERATORS[node['op']](attrs, *[constants[source] for source in sources])
                known.add(index)
            else:
                release = [source for source, last in last_use.items() if last == index]
                steps.append((index, OPERATORS[node['op']], attrs, sources, release))
        return constants, steps

    def forward(self, data):
        """
        Args:
            data (np.ndarray): Shape (N, 1, H, W), float32

        Returns:
            np.ndarray: Shape (T * N, num_classes)
        """
        outputs = list(self.constants)
        outputs[self.data_index] = data
        for index, operator, attrs, sources, release in self.steps:
            outputs[index] = operator(attrs, *[outputs[source] for source in sources])
            for source in release:
                outputs[source] = None
        return outputs[-1]


class NumpyOcr(AlOcrBase):
    MODEL_FILE_PREFIX = 'cnocr-v1.2.0'

    def __init__(
            self,
            model_name='densenet-lite-gru',
            model_epoch=None,
            cand_alphabet=None,
            root='',
            context='cpu',
            name=None,
    ):
        self._args = (model_name, model_epoch, root)
        # Width is reduced by 4 in densenet-lite-gru
        self._hp = SimpleNamespace(img_height=32, seq_len_cmpr_ratio=4)
        self._model_loaded = False
        self._cand_alph_idx = None

    def init(self):
        model_name, model_epoch, root = self._args
        prefix = f'{self.MODEL_FILE_PREFIX}-{model_name}'
        symbol = os.path.join(root, f'{prefix}-symbol.json')
        params = os.path.join(root, '%s-%04d.params' % (prefix, model_epoch))
        charset = os.path.join(root, 'label_cn.txt')
        for file in [symbol, params, charset]:
            if not os.path.exists(file):
                logger.warning(f'Ocr model not prepared: {root}')
                logger.warning(f'Required file: {file}')
                logger.critical('Please check if required files of pre-trained OCR model exist')
                raise RequestHumanTakeover

        logger.info('Loading OCR model: %s' % root)
        self._alphabet = [None]
        with open(charset, encoding='utf-8') as f:
            for line in f:
                self._alphabet.append(line.rstrip('\n'))
        self._alphabet = [' ' if char == '<space>' else char for char in self._alphabet]
        self._inv_alph_dict = {char: index for index, char in enumerate(self._alphabet)}
        self._network = NumpyNetwork(symbol, params)
        self._model_loaded = True

    def ensure_init(self):
        if not self._model_loaded:
            self.init()

    def set_cand_alphabet(self, cand_alphabet):
        self.ensure_init()
        if cand_alphabet is None:
            self._cand_alph_idx = None
        else:
            self._cand_alph_idx = sorted([0] + [self._inv_alph_dict[char] for char in cand_alphabet])

    def _pad_arrays(self, img_list):
        img_widths = [img.shape[2] for img in img_list]
        max_width = max(img_widths)
        return [
            np.pad(img, ((0, 0), (0, 0), (0, max_width - img.shape[2])), 'constant', constant_values=0.0)
            for img in img_list
        ], img_widths

    def ocr_for_single_lines(self, img_list):
        """
        Args:
            img_list (list[np.ndarray]): Gray images, shape (height, width)

        Returns:
            list[list[str]]:
        """
        self.ensure_init()
        if len(img_list) == 0:
            return []

        img_list, img_widths = self._pad_arrays([self._preprocess_img_array(img) for img in img_list])
        batch_size = len(img_list)
        prob = self._network.forward(np.stack(img_list))
        # [seq_len, batch_size, num_classes]
        prob = prob.reshape((-1, batch_size, prob.shape[1]))

        if self._cand_alph_idx is not None:
            mask = np.zeros(prob.shape[2], dtype='int8')
            mask[self._cand_alph_idx] = 1
            prob = prob * mask

        max_width = max(img_widths)
        return [self._gen_line_pred_chars(prob[:, i, :], img_widths[i], max_width) for i in range(batch_size)]

    def ocr_for_single_line(self, img_fp):
        return self.ocr_for_single_lines([img_fp])[0]

    def atomic_ocr_for_single_lines(self, img_list, cand_alphabet=None):
        self.set_cand_alphabet(cand_alphabet)
        return self.ocr_for_single_lines(img_list)

    def atomic_ocr_for_single_line(self, img_fp, cand_alphabet=None):
        self.set_cand_alphabet(cand_alphabet)
        return self.ocr_for_single_line(img_fp)

    def ocr(self, img_fp):
        """
        Args:
            img_fp (np.ndarray): Image of multiple lines, shape (height, width) or (height, width, 3)

        Returns:
            list[list[str]]:
        """
        self.ensure_init()
        img = img_fp
        if img.ndim == 3:
            img = np.array(Image.fromarray(img).convert('L'))
        if min(img.shape) < 2:
            return ''
        if img.mean() < 145:  # Black background to white background
            img = 255 - img
        return self.ocr_for_single_lines([line for line, _ in line_split(img, blank=True)])

    def atomic_ocr(self, img_fp, cand_alphabet=None):
        self.set_cand_alphabet(cand_alphabet)
        return self.ocr(img_fp)

    def debug(self, img_list):
        """
        Args:
            img_list (list[np.ndarray]): Shape (height, width)
        """
        img_list = [(self._preprocess_img_array(img) * 255.0).astype(np.uint8) for img in img_list]
        img_list, _ = self._pad_arrays(img_list)
        Image.fromarray(np.concatenate(img_list, axis=1)[0]).show()
