"""Tests for the numpy OCR backend, using the models in ./bin/cnocr_models (no mxnet, no device interaction)."""
import builtins
import importlib
import os
import sys
import types
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from dev_tools.ocr_golden import MODELS
from module.base.utils import extract_letters
from module.ocr import backend
from module.ocr.al_ocr_base import AlOcrBase
from module.ocr.models import OcrModel
from module.ocr.numpy_ocr import NumpyNetwork, NumpyOcr, check_node, gru_layer, load_mxnet_params, op_convolution

HERE = os.path.dirname(os.path.abspath(__file__))
SCREENSHOT = os.path.join(HERE, '../commission/commission_unmet_limitation_jp.png')
GOLDEN = os.path.join(HERE, 'mxnet_golden.npz')
UNLIMITED = ['無', '制', '限']


def get_model(lang):
    return NumpyOcr(model_epoch=MODELS[lang], root=f'./bin/cnocr_models/{lang}', name=lang)


def get_files(lang):
    prefix = f'./bin/cnocr_models/{lang}/cnocr-v1.2.0-densenet-lite-gru'
    return f'{prefix}-symbol.json', f'{prefix}-{MODELS[lang]:04d}.params'


def get_unlimited_text():
    # 無制限, yellow letters on a dark background
    image = np.array(Image.open(SCREENSHOT).convert('RGB'))[33:58, 975:1045]
    letter = tuple(int(c) for c in image.reshape(-1, 3)[np.argmax(image.reshape(-1, 3).sum(axis=1))])
    return extract_letters(image, letter=letter, threshold=128).astype(np.uint8)


class TestLoadParams(unittest.TestCase):
    def test_all_models(self):
        for lang in MODELS:
            params = load_mxnet_params(get_files(lang)[1])
            self.assertEqual(params['densenet0_stage0_conv0_weight'].shape, (32, 1, 3, 3))
            self.assertEqual(params['gru0_l0_i2h_weight'].shape[0], 3 * 128)
            self.assertEqual(params['gru0_r0_h2h_bias'].shape, (3 * 128,))


class TestOperators(unittest.TestCase):
    def test_gru_zero_input_keeps_zero_state(self):
        hidden, size = 2, 3
        out = gru_layer(
            np.zeros((4, 1, size), dtype='float32'),
            np.ones((3 * hidden, size), dtype='float32'),
            np.ones((3 * hidden, hidden), dtype='float32'),
            np.zeros(3 * hidden, dtype='float32'),
            np.zeros(3 * hidden, dtype='float32'),
        )
        self.assertEqual(out.shape, (4, 1, hidden))
        self.assertTrue(np.all(out == 0))

    def test_gru_empty_sequence(self):
        wx = np.zeros((6, 3), dtype='float32')
        wh = np.zeros((6, 2), dtype='float32')
        out = gru_layer(np.zeros((0, 5, 3), dtype='float32'), wx, wh, np.zeros(6), np.zeros(6))
        self.assertEqual(out.shape, (0, 5, 2))

    def test_convolution_1x1_with_stride(self):
        x = np.arange(16, dtype='float32').reshape((1, 1, 4, 4))
        attrs = {'pad': (0, 0), 'kernel': (1, 1), 'stride': (2, 2), 'num_group': 1}
        out = op_convolution(attrs, x, np.ones((1, 1, 1, 1), dtype='float32'))
        self.assertEqual(out.tolist(), [[[[0, 2], [8, 10]]]])

    def test_convolution_group_must_be_depthwise(self):
        attrs = {'pad': (0, 0), 'kernel': (1, 1), 'stride': (1, 1), 'num_group': 2}
        with self.assertRaises(ValueError):
            op_convolution(attrs, np.zeros((1, 4, 2, 2)), np.zeros((4, 2, 1, 1)))

    def test_check_node(self):
        check_node('null', {})
        check_node('RNN', {'bidirectional': True, 'mode': 'gru', 'num_layers': 1})
        with self.assertRaises(ValueError):
            check_node('RNN', {'bidirectional': True, 'mode': 'lstm', 'num_layers': 1})
        with self.assertRaises(ValueError):
            check_node('Activation', {'act_type': 'tanh'})
        with self.assertRaises(ValueError):
            check_node('Unknown', {})

    def test_all_models_pass_check(self):
        for lang in MODELS:
            NumpyNetwork(*get_files(lang))


class TestNumpyOcr(unittest.TestCase):
    def test_real_screenshot(self):
        # Models with a large charset depend on the backward GRU layer
        # This reads garbage if GRU parameters are unpacked in a wrong order
        self.assertEqual(get_model('jp').atomic_ocr_for_single_line(get_unlimited_text()), UNLIMITED)

    def test_batch_is_same_as_single(self):
        model = get_model('jp')
        text = get_unlimited_text()
        wide = np.full((25, 200), 255, dtype=np.uint8)
        wide[:, 60:130] = text
        single = [model.ocr_for_single_line(text), model.ocr_for_single_line(wide)]
        self.assertEqual(single[0], UNLIMITED)
        self.assertEqual(model.ocr_for_single_lines([text, wide]), single)

    def test_narrow_image(self):
        model = get_model('jp')
        for width in [1, 2, 3, 4]:
            self.assertIsInstance(model.ocr_for_single_line(np.full((32, width), 255, dtype=np.uint8)), list)
        # Width is rounded to 0 when resizing
        self.assertEqual(model.ocr_for_single_line(np.full((200, 1), 255, dtype=np.uint8)), [])

    def test_empty(self):
        self.assertEqual(get_model('azur_lane').atomic_ocr_for_single_lines([]), [])

    def test_cand_alphabet(self):
        model = get_model('jp')
        model.set_cand_alphabet('0123456789')
        self.assertEqual(model._cand_alph_idx[0], 0)
        self.assertEqual(len(model._cand_alph_idx), 11)
        result = model.ocr_for_single_line(get_unlimited_text())
        self.assertTrue(all(char in '0123456789' for char in result))
        model.set_cand_alphabet(None)
        self.assertIsNone(model._cand_alph_idx)

    def test_ctc_label(self):
        self.assertEqual(NumpyOcr._ctc_label([0, 1, 1, 0, 1, 2, 2, 0]), [1, 1, 2])

    def test_low_confidence_removed(self):
        model = get_model('azur_lane')
        model.ensure_init()
        prob = np.zeros((2, len(model._alphabet)), dtype='float32')
        prob[0, 1] = 0.9
        prob[1, 2] = 0.4
        self.assertEqual(model._gen_line_pred_chars(prob, 8, 8), [model._alphabet[1]])

    def test_narrow_image_in_batch_is_cut(self):
        model = get_model('azur_lane')
        model.ensure_init()
        prob = np.zeros((4, len(model._alphabet)), dtype='float32')
        prob[[0, 3], [1, 2]] = 1
        self.assertEqual(model._gen_line_pred_chars(prob, 8, 16), [model._alphabet[1]])

    def test_multi_line(self):
        text = get_unlimited_text()
        gap = np.full((12, text.shape[1]), 255, dtype=np.uint8)
        self.assertEqual(get_model('jp').ocr(np.vstack([text, gap, text])), [UNLIMITED, UNLIMITED])

    def test_multi_line_black_background(self):
        self.assertEqual(get_model('jp').atomic_ocr(255 - get_unlimited_text()), [UNLIMITED])

    def test_multi_line_tiny_image(self):
        self.assertEqual(get_model('jp').ocr(np.zeros((1, 10), dtype=np.uint8)), '')

    def test_debug(self):
        with patch.object(Image.Image, 'show') as show:
            get_model('jp').debug([get_unlimited_text(), np.full((25, 200), 255, dtype=np.uint8)])
        show.assert_called_once()


def get_fake_cnocr():
    cnocr = types.ModuleType('cnocr')
    cnocr.CnOcr = type('CnOcr', (), {})
    cn_ocr = types.ModuleType('cnocr.cn_ocr')
    cn_ocr.check_model_name = cn_ocr.gen_network = cn_ocr.load_module = cn_ocr.read_charset = None
    cn_ocr.data_dir = lambda: ''
    hyperparams = types.ModuleType('cnocr.hyperparams.cn_hyperparams')
    hyperparams.CnHyperparams = None
    return {
        'cnocr': cnocr,
        'cnocr.cn_ocr': cn_ocr,
        'cnocr.hyperparams': types.ModuleType('cnocr.hyperparams'),
        'cnocr.hyperparams.cn_hyperparams': hyperparams,
    }


class TestAlOcr(unittest.TestCase):
    def test_shared_code(self):
        # Real cnocr is not available on every platform, this is about imports and inheritance
        with patch.dict(sys.modules, get_fake_cnocr()):
            sys.modules.pop('module.ocr.al_ocr', None)
            al_ocr = importlib.import_module('module.ocr.al_ocr')
        self.assertIs(al_ocr.AlOcr._preprocess_img_array, AlOcrBase._preprocess_img_array)
        self.assertIs(al_ocr.AlOcr._gen_line_pred_chars, AlOcrBase._gen_line_pred_chars)


class TestBackend(unittest.TestCase):
    def reload_backend(self, modules):
        self.addCleanup(importlib.reload, backend)
        with patch.dict(sys.modules, modules):
            if 'module.ocr.al_ocr' not in modules:
                sys.modules.pop('module.ocr.al_ocr', None)
            return importlib.reload(backend).AlOcr

    def test_fallback_without_cnocr(self):
        self.assertIs(self.reload_backend({'cnocr': None}), NumpyOcr)

    def test_prefer_cnocr(self):
        module = types.ModuleType('module.ocr.al_ocr')
        module.AlOcr = object()
        self.assertIs(self.reload_backend({'module.ocr.al_ocr': module}), module.AlOcr)

    def test_other_import_errors_are_raised(self):
        with self.assertRaises(ModuleNotFoundError):
            self.reload_backend({'module.ocr.al_ocr': None})

    def test_broken_mxnet_is_raised(self):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == 'module.ocr.al_ocr':
                raise OSError('Library not loaded')
            return real_import(name, *args, **kwargs)

        self.addCleanup(importlib.reload, backend)
        with patch('builtins.__import__', fake_import):
            with self.assertRaises(OSError):
                importlib.reload(backend)

    def test_models_are_built_from_backend(self):
        class FakeOcr:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        with patch.object(backend, 'AlOcr', FakeOcr):
            for lang, epoch in MODELS.items():
                model = getattr(OcrModel(), lang)
                self.assertIsInstance(model, FakeOcr)
                self.assertEqual(model.kwargs['model_epoch'], epoch)


@unittest.skipUnless(os.path.exists(GOLDEN), 'Generate tests/ocr/mxnet_golden.npz by dev_tools/ocr_golden.py')
class TestMxnetGolden(unittest.TestCase):
    def test_same_as_mxnet(self):
        golden = np.load(GOLDEN)
        for lang in MODELS:
            prob = NumpyNetwork(*get_files(lang)).forward(golden[f'{lang}_input'])
            self.assertTrue(np.allclose(prob, golden[f'{lang}_prob'], atol=1e-3), lang)


if __name__ == '__main__':
    unittest.main()
