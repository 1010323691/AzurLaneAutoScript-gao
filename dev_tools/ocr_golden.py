"""
Save real MXNet outputs, so NumpyOcr can be compared with them in tests/ocr/test_numpy_ocr.py

Must run on a machine with cnocr and mxnet, under the root folder of Alas:
    ./toolkit/python.exe -m dev_tools.ocr_golden
Then copy tests/ocr/mxnet_golden.npz to the machine that runs NumpyOcr.
"""
import cv2
import numpy as np

from module.base.utils import crop, load_image
from module.logger import logger

SCREENSHOT = './tests/commission/commission_unmet_limitation_jp.png'
GOLDEN = './tests/ocr/mxnet_golden.npz'
MODELS = {'azur_lane': 15, 'azur_lane_jp': 20, 'cnocr': 39, 'jp': 125, 'tw': 63}
# Text lines in the screenshot, (x1, y1, x2, y2)
AREAS = [(975, 33, 1045, 58), (668, 33, 780, 58), (326, 32, 556, 58)]


def load_lines():
    image = load_image(SCREENSHOT)
    return [cv2.cvtColor(crop(image, area), cv2.COLOR_RGB2GRAY) for area in AREAS]


def main():
    # Import directly, this should fail if mxnet is not available
    import mxnet as mx
    from module.ocr.al_ocr import AlOcr

    lines = load_lines()
    result = {}
    for lang, epoch in MODELS.items():
        model = AlOcr(model_name='densenet-lite-gru', model_epoch=epoch, root=f'./bin/cnocr_models/{lang}', name=lang)
        model.init(*model._args)
        batch, _ = model._pad_arrays([model._preprocess_img_array(line) for line in lines])
        # Save inputs as well, cv2.resize may differ between opencv versions
        result[f'{lang}_input'] = np.stack(batch)
        result[f'{lang}_prob'] = model._predict(mx.nd.array(batch))

    np.savez_compressed(GOLDEN, **result)
    logger.info(f'Saved: {GOLDEN}')


if __name__ == '__main__':
    main()
