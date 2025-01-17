# ------------------------------------------------------------------------------
# Copyright (c) Microsoft
# Licensed under the MIT License.
# Written by Bin Xiao (Bin.Xiao@microsoft.com)
# ------------------------------------------------------------------------------

from __future__ import absolute_import, division, print_function

import numpy as np
from core.inference import get_max_preds


def calc_dists(preds, target, normalize):
    preds = preds.astype(np.float32)
    target = target.astype(np.float32)
    dists = np.zeros((preds.shape[1], preds.shape[0]))
    for n in range(preds.shape[0]):
        for c in range(preds.shape[1]):
            if target[n, c, 0] > 1 and target[n, c, 1] > 1:
                normed_preds = preds[n, c, :] / normalize[n]
                normed_targets = target[n, c, :] / normalize[n]
                dists[c, n] = np.linalg.norm(normed_preds - normed_targets)
            else:
                dists[c, n] = -1
    return dists


def dist_acc(dists, thr=0.5):
    """Return percentage below threshold while ignoring values with a -1"""
    dist_cal = np.not_equal(dists, -1)
    num_dist_cal = dist_cal.sum()
    if num_dist_cal > 0:
        return np.less(dists[dist_cal], thr).sum() * 1.0 / num_dist_cal
    else:
        return -1


def accuracy(output, target, hm_type="gaussian", thr=0.5):
    """
    Calculate accuracy according to PCK,
    but uses ground truth heatmap rather than x,y locations
    First value to be returned is average accuracy across 'idxs',
    followed by individual accuracies
    """
    idx = list(range(output.shape[1]))
    norm = 1.0
    if hm_type == "gaussian":
        pred, _ = get_max_preds(output)
        target, _ = get_max_preds(target)
        h = output.shape[2]
        w = output.shape[3]
        norm = np.ones((pred.shape[0], 2)) * np.array([h, w]) / 10
    dists = calc_dists(pred, target, norm)

    acc = np.zeros((len(idx) + 1))
    avg_acc = 0
    cnt = 0

    for i in range(len(idx)):
        acc[i + 1] = dist_acc(dists[idx[i]])
        if acc[i + 1] >= 0:
            avg_acc = avg_acc + acc[i + 1]
            cnt += 1

    avg_acc = avg_acc / cnt if cnt != 0 else 0
    if cnt != 0:
        acc[0] = avg_acc
    return acc, avg_acc, cnt, pred


def get_acc(idx, dists):
    acc = np.zeros((len(idx) + 1))
    avg_acc = 0
    cnt = 0

    for i in range(len(idx)):
        acc[i + 1] = dist_acc(dists[idx[i]])
        if acc[i + 1] >= 0:
            avg_acc = avg_acc + acc[i + 1]
            cnt += 1

    avg_acc = avg_acc / cnt if cnt != 0 else 0
    if cnt != 0:
        acc[0] = avg_acc
    return acc, avg_acc, cnt


def accuracy_infinity_coco(output, target, hm_type="gaussian", thr=0.5):
    """
    Calculate accuracy according to PCK,
    but uses ground truth heatmap rather than x,y locations
    First value to be returned is average accuracy across 'idxs',
    followed by individual accuracies
    """
    idx = list(range(output.shape[1]))
    infinity_idxs = np.any(np.sum(target, axis=(2, 3))[:, 17:] > 1, axis=1)
    norm = 1.0
    if hm_type == "gaussian":
        pred, _ = get_max_preds(output)
        target, _ = get_max_preds(target)
        h = output.shape[2]
        w = output.shape[3]
        norm = np.ones((pred.shape[0], 2)) * np.array([h, w]) / 10
    pred_infinity = pred.copy()[infinity_idxs]
    pred_anatomical = pred.copy()[infinity_idxs][:, 17:, :]
    pred_coco = pred.copy()[~infinity_idxs][:, :17, :]

    norm_infinity = norm.copy()[infinity_idxs]
    norm_anatomical = norm.copy()[infinity_idxs]
    norm_coco = norm.copy()[~infinity_idxs]

    target_infinity = target.copy()[infinity_idxs]
    target_anatomical = target.copy()[infinity_idxs][:, 17:, :]
    target_coco = target.copy()[~infinity_idxs][:, :17, :]

    dists_infinity = calc_dists(pred_infinity, target_infinity, norm_infinity)
    dists_coco = calc_dists(pred_coco, target_coco, norm_coco)
    dists_anatomical = calc_dists(pred_anatomical, target_anatomical, norm_anatomical)

    acc_infinity, avg_acc_infinity, cnt_infinity = get_acc(idx, dists_infinity)
    acc_coco, avg_acc_coco, cnt_coco = get_acc(list(range(17)), dists_coco)
    acc_anatomical, avg_acc_anatomical, cnt_anatomical = get_acc(
        list(range(output.shape[1] - 17)), dists_anatomical
    )

    return (
        (acc_infinity, avg_acc_infinity, cnt_infinity),
        (acc_anatomical, avg_acc_anatomical, cnt_anatomical),
        (acc_coco, avg_acc_coco, cnt_coco),
        pred,
    )
