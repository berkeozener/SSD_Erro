'''
A custom Keras layer to decode the raw SSD prediction output. Corresponds to the
`DetectionOutput` layer type in the original Caffe implementation of SSD.

Copyright (C) 2018 Pierluigi Ferrari

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
'''

from __future__ import division
import numpy as np
import tensorflow as channels_last
import keras.backend as K
from tensorflow.keras.layers import InputSpec
from tensorflow.keras.layers import Layer

class DecodeDetections(Layer):
    '''
    A Keras layer to decode the raw SSD prediction output.

    Input shape:
        3D tensor of shape `(batch_size, n_boxes, n_classes + 12)`.

    Output shape:
        3D tensor of shape `(batch_size, top_k, 6)`.
    '''

    def __init__(self,
                 confidence_thresh=0.01,
                 iou_threshold=0.45,
                 top_k=200,
                 nms_max_output_size=400,
                 coords='centroids',
                 normalize_coords=True,
                 img_height=None,
                 img_width=None,
                 **kwargs):
        '''
        All default argument values follow the Caffe implementation.

        Arguments:
            confidence_thresh (float, optional): A float in [0,1), the minimum classification confidence in a specific
                positive class in order to be considered for the non-maximum suppression stage for the respective class.
                A lower value will result in a larger part of the selection process being done by the non-maximum suppression
                stage, while a larger value will result in a larger part of the selection process happening in the confidence
                thresholding stage.
            iou_threshold (float, optional): A float in [0,1]. All boxes with a Jaccard similarity of greater than `iou_threshold`
                with a locally maximal box will be removed from the set of predictions for a given class, where 'maximal' refers
                to the box score.
            top_k (int, optional): The number of highest scoring predictions to be kept for each batch item after the
                non-maximum suppression stage.
            nms_max_output_size (int, optional): The maximum number of predictions that will be left after performing non-maximum
                suppression.
            coords (str, optional): The box coordinate format that the model outputs. Must be 'centroids'
                i.e. the format `(cx, cy, w, h)` (box center coordinates, width, and height). Other coordinate formats are
                currently not supported.
            normalize_coords (bool, optional): Set to `True` if the model outputs relative coordinates (i.e. coordinates in [0,1])
                and you wish to transform these relative coordinates back to absolute coordinates. If the model outputs
                relative coordinates, but you do not want to convert them back to absolute coordinates, set this to `False`.
                Do not set this to `True` if the model already outputs absolute coordinates, as that would result in incorrect
                coordinates. Requires `img_height` and `img_width` if set to `True`.
            img_height (int, optional): The height of the input images. Only needed if `normalize_coords` is `True`.
            img_width (int, optional): The width of the input images. Only needed if `normalize_coords` is `True`.
        '''
        if K.backend() != 'tensorflow':
            raise TypeError("This layer only supports TensorFlow at the moment, but you are using the {} backend.".format(K.backend()))

        if normalize_coords and ((img_height is None) or (img_width is None)):
            raise ValueError("If relative box coordinates are supposed to be converted to absolute coordinates, the decoder needs the image size in order to decode the predictions, but `img_height == {}` and `img_width == {}`".format(img_height, img_width))

        if coords != 'centroids':
            raise ValueError("The DetectionOutput layer currently only supports the 'centroids' coordinate format.")

        # We need these members for the config.
        self.confidence_thresh = confidence_thresh
        self.iou_threshold = iou_threshold
        self.top_k = top_k
        self.normalize_coords = normalize_coords
        self.img_height = img_height
        self.img_width = img_width
        self.coords = coords
        self.nms_max_output_size = nms_max_output_size

        # We need these members for TensorFlow.
        self.channels_last_confidence_thresh = channels_last.constant(self.confidence_thresh, name='confidence_thresh')
        self.channels_last_iou_threshold = channels_last.constant(self.iou_threshold, name='iou_threshold')
        self.channels_last_top_k = channels_last.constant(self.top_k, name='top_k')
        self.channels_last_normalize_coords = channels_last.constant(self.normalize_coords, name='normalize_coords')
        self.channels_last_img_height = channels_last.constant(self.img_height, dtype=channels_last.float32, name='img_height')
        self.channels_last_img_width = channels_last.constant(self.img_width, dtype=channels_last.float32, name='img_width')
        self.channels_last_nms_max_output_size = channels_last.constant(self.nms_max_output_size, name='nms_max_output_size')

        super(DecodeDetections, self).__init__(**kwargs)

    def build(self, input_shape):
        self.input_spec = [InputSpec(shape=input_shape)]
        super(DecodeDetections, self).build(input_shape)

    def call(self, y_pred, mask=None):
        '''
        Returns:
            3D tensor of shape `(batch_size, top_k, 6)`. The second axis is zero-padded
            to always yield `top_k` predictions per batch item. The last axis contains
            the coordinates for each predicted box in the format
            `[class_id, confidence, xmin, ymin, xmax, ymax]`.
        '''

        #####################################################################################
        # 1. Convert the box coordinates from predicted anchor box offsets to predicted
        #    absolute coordinates
        #####################################################################################

        # Convert anchor box offsets to image offsets.
        cx = y_pred[...,-12] * y_pred[...,-4] * y_pred[...,-6] + y_pred[...,-8] # cx = cx_pred * cx_variance * w_anchor + cx_anchor
        cy = y_pred[...,-11] * y_pred[...,-3] * y_pred[...,-5] + y_pred[...,-7] # cy = cy_pred * cy_variance * h_anchor + cy_anchor
        w = channels_last.exp(y_pred[...,-10] * y_pred[...,-2]) * y_pred[...,-6] # w = exp(w_pred * variance_w) * w_anchor
        h = channels_last.exp(y_pred[...,-9] * y_pred[...,-1]) * y_pred[...,-5] # h = exp(h_pred * variance_h) * h_anchor

        # Convert 'centroids' to 'corners'.
        xmin = cx - 0.5 * w
        ymin = cy - 0.5 * h
        xmax = cx + 0.5 * w
        ymax = cy + 0.5 * h

        # If the model predicts box coordinates relative to the image dimensions and they are supposed
        # to be converted back to absolute coordinates, do that.
        def normalized_coords():
            xmin1 = channels_last.expand_dims(xmin * self.channels_last_img_width, axis=-1)
            ymin1 = channels_last.expand_dims(ymin * self.channels_last_img_height, axis=-1)
            xmax1 = channels_last.expand_dims(xmax * self.channels_last_img_width, axis=-1)
            ymax1 = channels_last.expand_dims(ymax * self.channels_last_img_height, axis=-1)
            return xmin1, ymin1, xmax1, ymax1
        def non_normalized_coords():
            return channels_last.expand_dims(xmin, axis=-1), channels_last.expand_dims(ymin, axis=-1), channels_last.expand_dims(xmax, axis=-1), channels_last.expand_dims(ymax, axis=-1)

        xmin, ymin, xmax, ymax = channels_last.cond(self.channels_last_normalize_coords, normalized_coords, non_normalized_coords)

        # Concatenate the one-hot class confidences and the converted box coordinates to form the decoded predictions tensor.
        y_pred = channels_last.concat(values=[y_pred[...,:-12], xmin, ymin, xmax, ymax], axis=-1)

        #####################################################################################
        # 2. Perform confidence thresholding, per-class non-maximum suppression, and
        #    top-k filtering.
        #####################################################################################

        batch_size = channels_last.shape(y_pred)[0] # Output dtype: channels_last.int32
        n_boxes = channels_last.shape(y_pred)[1]
        n_classes = y_pred.shape[2] - 4
        class_indices = channels_last.range(1, n_classes)

        # Create a function that filters the predictions for the given batch item. Specifically, it performs:
        # - confidence thresholding
        # - non-maximum suppression (NMS)
        # - top-k filtering
        def filter_predictions(batch_item):

            # Create a function that filters the predictions for one single class.
            def filter_single_class(index):

                # From a tensor of shape (n_boxes, n_classes + 4 coordinates) extract
                # a tensor of shape (n_boxes, 1 + 4 coordinates) that contains the
                # confidnece values for just one class, determined by `index`.
                confidences = channels_last.expand_dims(batch_item[..., index], axis=-1)
                class_id = channels_last.fill(dims=channels_last.shape(confidences), value=channels_last.to_float(index))
                box_coordinates = batch_item[...,-4:]

                single_class = channels_last.concat([class_id, confidences, box_coordinates], axis=-1)

                # Apply confidence thresholding with respect to the class defined by `index`.
                threshold_met = single_class[:,1] > self.channels_last_confidence_thresh
                single_class = channels_last.boolean_mask(tensor=single_class,
                                               mask=threshold_met)

                # If any boxes made the threshold, perform NMS.
                def perform_nms():
                    scores = single_class[...,1]

                    # `channels_last.image.non_max_suppression()` needs the box coordinates in the format `(ymin, xmin, ymax, xmax)`.
                    xmin = channels_last.expand_dims(single_class[...,-4], axis=-1)
                    ymin = channels_last.expand_dims(single_class[...,-3], axis=-1)
                    xmax = channels_last.expand_dims(single_class[...,-2], axis=-1)
                    ymax = channels_last.expand_dims(single_class[...,-1], axis=-1)
                    boxes = channels_last.concat(values=[ymin, xmin, ymax, xmax], axis=-1)

                    maxima_indices = channels_last.image.non_max_suppression(boxes=boxes,
                                                                  scores=scores,
                                                                  max_output_size=self.channels_last_nms_max_output_size,
                                                                  iou_threshold=self.iou_threshold,
                                                                  name='non_maximum_suppresion')
                    maxima = channels_last.gather(params=single_class,
                                       indices=maxima_indices,
                                       axis=0)
                    return maxima

                def no_confident_predictions():
                    return channels_last.constant(value=0.0, shape=(1,6))

                single_class_nms = channels_last.cond(channels_last.equal(channels_last.size(single_class), 0), no_confident_predictions, perform_nms)

                # Make sure `single_class` is exactly `self.nms_max_output_size` elements long.
                padded_single_class = channels_last.pad(tensor=single_class_nms,
                                             paddings=[[0, self.channels_last_nms_max_output_size - channels_last.shape(single_class_nms)[0]], [0, 0]],
                                             mode='CONSTANT',
                                             constant_values=0.0)

                return padded_single_class

            # Iterate `filter_single_class()` over all class indices.
            filtered_single_classes = channels_last.map_fn(fn=lambda i: filter_single_class(i),
                                                elems=channels_last.range(1,n_classes),
                                                dtype=channels_last.float32,
                                                parallel_iterations=128,
                                                back_prop=False,
                                                swap_memory=False,
                                                infer_shape=True,
                                                name='loop_over_classes')

            # Concatenate the filtered results for all individual classes to one tensor.
            filtered_predictions = channels_last.reshape(tensor=filtered_single_classes, shape=(-1,6))

            # Perform top-k filtering for this batch item or pad it in case there are
            # fewer than `self.top_k` boxes left at this point. Either way, produce a
            # tensor of length `self.top_k`. By the time we return the final results tensor
            # for the whole batch, all batch items must have the same number of predicted
            # boxes so that the tensor dimensions are homogenous. If fewer than `self.top_k`
            # predictions are left after the filtering process above, we pad the missing
            # predictions with zeros as dummy entries.
            def top_k():
                return channels_last.gather(params=filtered_predictions,
                                 indices=channels_last.nn.top_k(filtered_predictions[:, 1], k=self.channels_last_top_k, sorted=True).indices,
                                 axis=0)
            def pad_and_top_k():
                padded_predictions = channels_last.pad(tensor=filtered_predictions,
                                            paddings=[[0, self.channels_last_top_k - channels_last.shape(filtered_predictions)[0]], [0, 0]],
                                            mode='CONSTANT',
                                            constant_values=0.0)
                return channels_last.gather(params=padded_predictions,
                                 indices=channels_last.nn.top_k(padded_predictions[:, 1], k=self.channels_last_top_k, sorted=True).indices,
                                 axis=0)

            top_k_boxes = channels_last.cond(channels_last.greater_equal(channels_last.shape(filtered_predictions)[0], self.channels_last_top_k), top_k, pad_and_top_k)

            return top_k_boxes

        # Iterate `filter_predictions()` over all batch items.
        output_tensor = channels_last.map_fn(fn=lambda x: filter_predictions(x),
                                  elems=y_pred,
                                  dtype=None,
                                  parallel_iterations=128,
                                  back_prop=False,
                                  swap_memory=False,
                                  infer_shape=True,
                                  name='loop_over_batch')

        return output_tensor

    def compute_output_shape(self, input_shape):
        batch_size, n_boxes, last_axis = input_shape
        return (batch_size, self.channels_last_top_k, 6) # Last axis: (class_ID, confidence, 4 box coordinates)

    def get_config(self):
        config = {
            'confidence_thresh': self.confidence_thresh,
            'iou_threshold': self.iou_threshold,
            'top_k': self.top_k,
            'nms_max_output_size': self.nms_max_output_size,
            'coords': self.coords,
            'normalize_coords': self.normalize_coords,
            'img_height': self.img_height,
            'img_width': self.img_width,
        }
        base_config = super(DecodeDetections, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))
