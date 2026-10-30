import os

import cv2
import numpy as np
from PIL import Image

from module.exception import RequestHumanTakeover
from module.logger import logger

logger.info('Loading OCR dependencies')
from cnocr import CnOcr
from cnocr.cn_ocr import (check_model_name, data_dir, gen_network, load_module,
                          read_charset)
from cnocr.hyperparams.cn_hyperparams import CnHyperparams as Hyperparams
from module.device.pkg_resources import PACKAGE_CACHE
from module.ocr.al_ocr_base import AlOcrBase


def get_mxnet_context():
    for dist in PACKAGE_CACHE.dict_installed_packages.values():
        # mxnet_cu101
        if dist.dist.startswith('mxnet_cu'):
            logger.info(f'MXNet gpu package: {dist.dist}=={dist.version} found, using it')
            return 'gpu'

    return 'cpu'


class AlOcr(AlOcrBase, CnOcr):
    # 'cpu' or 'gpu'
    # To use predict in gpu, the gpu version of mxnet must be installed.
    CNOCR_CONTEXT = get_mxnet_context()

    def __init__(
            self,
            model_name='densenet-lite-gru',
            model_epoch=None,
            cand_alphabet=None,
            root=data_dir(),
            context='cpu',
            name=None,
    ):
        self._args = (model_name, model_epoch, cand_alphabet, root, context, name)
        self._model_loaded = False

    def init(self,
             model_name='densenet-lite-gru',
             model_epoch=None,
             cand_alphabet=None,
             root=data_dir(),
             context='cpu',
             name=None,
             ):
        """

        :param model_name: 模型名称
        :param model_epoch: 模型迭代次数
        :param cand_alphabet: 待识别字符所在的候选集合。默认为 `None`，表示不限定识别字符范围
        :param root: 模型文件所在的根目录。
            Linux/Mac下默认值为 `~/.cnocr`，表示模型文件所处文件夹类似 `~/.cnocr/1.1.0/conv-lite-fc-0027`。
            Windows下默认值为 ``。
        :param context: 'cpu', or 'gpu'。表明预测时是使用CPU还是GPU。默认为CPU。
        :param name: 正在初始化的这个实例名称。如果需要同时初始化多个实例，需要为不同的实例指定不同的名称。
        """
        check_model_name(model_name)
        self._model_name = model_name
        self._model_file_prefix = '{}-{}'.format(self.MODEL_FILE_PREFIX, model_name)
        self._model_epoch = model_epoch

        self._model_dir = root  # Change folder structure.
        self._assert_and_prepare_model_files()
        self._alphabet, self._inv_alph_dict = read_charset(
            os.path.join(self._model_dir, 'label_cn.txt')
        )

        self._cand_alph_idx = None
        # Alphabet will be set before calling ocr.
        # self.set_cand_alphabet(cand_alphabet)

        self._hp = Hyperparams()
        self._hp._loss_type = None  # infer mode
        self._hp._num_classes = len(self._alphabet)
        # 传入''的话，也改成传入None
        self._net_prefix = None if name == '' else name

        self._mod = self._get_module(AlOcr.CNOCR_CONTEXT)

    def ocr(self, img_fp):
        if not self._model_loaded:
            self.init(*self._args)
            self._model_loaded = True

        return super().ocr(img_fp)

    def ocr_for_single_line(self, img_fp):
        if not self._model_loaded:
            self.init(*self._args)
            self._model_loaded = True

        return super().ocr_for_single_line(img_fp)

    def ocr_for_single_lines(self, img_list):
        if not self._model_loaded:
            self.init(*self._args)
            self._model_loaded = True

        return super().ocr_for_single_lines(img_list)

    def set_cand_alphabet(self, cand_alphabet):
        if not self._model_loaded:
            self.init(*self._args)
            self._model_loaded = True

        return super().set_cand_alphabet(cand_alphabet)

    """
    Atomic version of the OCR methods above
    handling set_cand_alphabet inside
    """

    def atomic_ocr(self, img_fp, cand_alphabet=None):
        if not self._model_loaded:
            self.init(*self._args)
            self._model_loaded = True

        super().set_cand_alphabet(cand_alphabet)

        return super().ocr(img_fp)

    def atomic_ocr_for_single_line(self, img_fp, cand_alphabet=None):
        if not self._model_loaded:
            self.init(*self._args)
            self._model_loaded = True

        super().set_cand_alphabet(cand_alphabet)

        return super().ocr_for_single_line(img_fp)

    def atomic_ocr_for_single_lines(self, img_list, cand_alphabet=None):
        if not self._model_loaded:
            self.init(*self._args)
            self._model_loaded = True

        super().set_cand_alphabet(cand_alphabet)

        return super().ocr_for_single_lines(img_list)

    def _assert_and_prepare_model_files(self):
        model_dir = self._model_dir
        model_files = [
            'label_cn.txt',
            '%s-%04d.params' % (self._model_file_prefix, self._model_epoch),
            '%s-symbol.json' % self._model_file_prefix,
        ]
        file_prepared = True
        for f in model_files:
            f = os.path.join(model_dir, f)
            if not os.path.exists(f):
                file_prepared = False
                logger.warning('can not find file %s', f)
                break

        if file_prepared:
            return

        # Disable auto downloading cnocr models when model not found.
        # get_model_file(model_dir)
        logger.warning(f'Ocr model not prepared: {model_dir}')
        logger.warning(f'Required files: {model_files}')
        logger.critical('Please check if required files of pre-trained OCR model exist')
        raise RequestHumanTakeover

    def _get_module(self, context):
        network, self._hp = gen_network(self._model_name, self._hp, self._net_prefix)
        hp = self._hp
        prefix = os.path.join(self._model_dir, self._model_file_prefix)
        data_names = ['data']
        data_shapes = [(data_names[0], (hp.batch_size, 1, hp.img_height, hp.img_width))]
        logger.info('Loading OCR model: %s' % self._model_dir)  # Change log appearance.
        mod = load_module(
            prefix,
            self._model_epoch,
            data_names,
            data_shapes,
            network=network,
            net_prefix=self._net_prefix,
            context=context,
        )
        return mod

    def debug(self, img_list):
        """
        Args:
            img_list: List of numpy array, (height, width)
        """
        self.init(*self._args)
        img_list = [(self._preprocess_img_array(img) * 255.0).astype(np.uint8) for img in img_list]
        img_list, img_widths = self._pad_arrays(img_list)
        image = cv2.hconcat(img_list)[0, :, :]
        Image.fromarray(image).show()
