import cv2
import numpy as np


class AlOcrBase:
    """
    Pre-process and decoding shared by AlOcr and NumpyOcr, without importing cnocr or mxnet.
    Subclasses should provide `self._hp` and `self._alphabet`.
    """

    def _preprocess_img_array(self, img):
        """
        :param img: image array with type mx.nd.NDArray or np.ndarray,
        with shape [height, width] or [height, width, channel].
        channel should be 1 (gray image) or 3 (color image).

        :return: np.ndarray, with shape (1, height, width)
        """
        # Resize image using `cv2.resize` instead of `mxnet.image.imresize`
        new_width = max(int(round(self._hp.img_height / img.shape[0] * img.shape[1])), 1)
        img = cv2.resize(img, (new_width, self._hp.img_height))
        img = np.expand_dims(img, 0).astype('float32') / 255.0
        return img

    @staticmethod
    def _ctc_label(class_ids):
        """
        Remove blanks and repeated labels.

        Args:
            class_ids (list[int]):

        Returns:
            list[int]:
        """
        labels, previous = [], 0
        for current in class_ids:
            if current != 0 and current != previous:
                labels.append(current)
            previous = current
        return labels

    def _gen_line_pred_chars(self, line_prob, img_width, max_img_width):
        """
        Get the predicted characters.
        :param line_prob: with shape of [seq_length, num_classes]
        :param img_width:
        :param max_img_width:
        :return:
        """
        class_ids = np.argmax(line_prob, axis=-1)

        class_ids *= np.max(line_prob, axis=-1) > 0.5  # Delete low confidence result

        if img_width < max_img_width:
            comp_ratio = self._hp.seq_len_cmpr_ratio
            end_idx = img_width // comp_ratio
            if end_idx < len(class_ids):
                class_ids[end_idx:] = 0
        prediction = self._ctc_label(class_ids.tolist())
        alphabet = self._alphabet
        res = [alphabet[p] if alphabet[p] != '<space>' else ' ' for p in prediction]

        return res
