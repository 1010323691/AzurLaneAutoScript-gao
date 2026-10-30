from module.logger import logger

try:
    from module.ocr.al_ocr import AlOcr
except ModuleNotFoundError as e:
    # Only when they are not installed, such as macOS on arm64
    # A broken installation should stop Alas instead of switching backend silently
    if e.name not in ('cnocr', 'mxnet'):
        raise
    logger.warning(f'{e.name} is not installed, use NumpyOcr instead')
    from module.ocr.numpy_ocr import NumpyOcr as AlOcr

__all__ = ['AlOcr']
