# ------------------------------------------------------------------------------
# Copyright (c) Microsoft
# Licensed under the MIT License.
# Written by Bin Xiao (Bin.Xiao@microsoft.com)
# ------------------------------------------------------------------------------

from __future__ import absolute_import, division, print_function

import logging
import os
import time

import numpy as np
import torch
import wandb
from core.evaluate import accuracy, accuracy_infinity_coco
from core.inference import get_final_preds
from utils.transforms import flip_back
from utils.vis import save_debug_images

logger = logging.getLogger(__name__)


def train(
    config,
    train_loader,
    model,
    criterion,
    optimizer,
    epoch,
    output_dir,
    tb_log_dir,
    writer_dict,
):
    batch_time = AverageMeter()
    data_time = AverageMeter()
    losses = AverageMeter()
    if config.MODEL.NUM_JOINTS == 53:
        acc_infinity = AverageMeter()
        acc_anatomical = AverageMeter()
        acc_coco = AverageMeter()
    else:
        acc = AverageMeter()

    # switch to train mode
    model.train()
    # freeze specified layers
    extra = config.MODEL.EXTRA
    if "FREEZE_LAYERS" in extra and extra["FREEZE_LAYERS"]:
        frozen_layers = extra.FROZEN_LAYERS
        for layer in frozen_layers:
            eval("model.module." + layer + ".requires_grad_(False)")

    end = time.time()
    for i, (input, target, target_weight, meta) in enumerate(train_loader):
        # measure data loading time
        data_time.update(time.time() - end)

        # compute output
        outputs = model(input)

        target = target.cuda(non_blocking=True)
        target_weight = target_weight.cuda(non_blocking=True)

        if isinstance(outputs, list):
            loss = criterion(outputs[0], target, target_weight)
            for output in outputs[1:]:
                loss += criterion(output, target, target_weight)
        else:
            output = outputs
            loss = criterion(output, target, target_weight)

        # loss = criterion(output, target, target_weight)

        # compute gradient and do update step
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        # measure accuracy and record loss
        losses.update(loss.item(), input.size(0))

        if config.MODEL.NUM_JOINTS == 53:
            (
                (_, avg_acc_infinity, cnt_infinity),
                (_, avg_acc_anatomical, cnt_anatomical),
                (_, avg_acc_coco, cnt_coco),
                pred,
            ) = accuracy_infinity_coco(
                output.detach().cpu().numpy(), target.detach().cpu().numpy()
            )
            acc_infinity.update(avg_acc_infinity, cnt_infinity)
            acc_anatomical.update(avg_acc_anatomical, cnt_anatomical)
            acc_coco.update(avg_acc_coco, cnt_coco)
        else:
            _, avg_acc, cnt, pred = accuracy(
                output.detach().cpu().numpy(), target.detach().cpu().numpy()
            )
            acc.update(avg_acc, cnt)

        # measure elapsed time
        batch_time.update(time.time() - end)
        end = time.time()

        if i % config.PRINT_FREQ == 0:
            if config.MODEL.NUM_JOINTS == 53:
                msg = (
                    "Epoch: [{0}][{1}/{2}]\t"
                    "Time {batch_time.val:.3f}s ({batch_time.avg:.3f}s)\t"
                    "Speed {speed:.1f} samples/s\t"
                    "Data {data_time.val:.3f}s ({data_time.avg:.3f}s)\t"
                    "Loss {loss.val:.5f} ({loss.avg:.5f})\t"
                    "Accuracy Infinity {acc_infinity.val:.3f} ({acc_infinity.avg:.3f})\t"
                    "Accuracy Anatomical {acc_anatomical.val:.3f} ({acc_anatomical.avg:.3f})\t"
                    "Accuracy COCO {acc_coco.val:.3f} ({acc_coco.avg:.3f})".format(
                        epoch,
                        i,
                        len(train_loader),
                        batch_time=batch_time,
                        speed=input.size(0) / batch_time.val,
                        data_time=data_time,
                        loss=losses,
                        acc_infinity=acc_infinity,
                        acc_anatomical=acc_anatomical,
                        acc_coco=acc_coco,
                    )
                )
            else:
                msg = (
                    "Epoch: [{0}][{1}/{2}]\t"
                    "Time {batch_time.val:.3f}s ({batch_time.avg:.3f}s)\t"
                    "Speed {speed:.1f} samples/s\t"
                    "Data {data_time.val:.3f}s ({data_time.avg:.3f}s)\t"
                    "Loss {loss.val:.5f} ({loss.avg:.5f})\t"
                    "Accuracy {acc.val:.3f} ({acc.avg:.3f})".format(
                        epoch,
                        i,
                        len(train_loader),
                        batch_time=batch_time,
                        speed=input.size(0) / batch_time.val,
                        data_time=data_time,
                        loss=losses,
                        acc=acc,
                    )
                )
            logger.info(msg)
            if config.LOG_WANDB:
                if config.MODEL.NUM_JOINTS == 53:
                    wandb.log(
                        {
                            "epoch": epoch,
                            "train/loss_avg": losses.avg,
                            "train/accuracy_infinity_avg": acc_infinity.avg,
                            "train/accuracy_anatomical_avg": acc_anatomical.avg,
                            "train/accuracy_coco_avg": acc_coco.avg,
                            "speed": input.size(0) / batch_time.val,
                        }
                    )
                else:
                    wandb.log(
                        {
                            "epoch": epoch,
                            "train/loss_avg": losses.avg,
                            "train/accuracy_avg": acc.avg,
                            "speed": input.size(0) / batch_time.val,
                        }
                    )

            writer = writer_dict["writer"]
            global_steps = writer_dict["train_global_steps"]
            writer.add_scalar("train_loss", losses.val, global_steps)
            if config.MODEL.NUM_JOINTS == 53:
                writer.add_scalar("train_acc_infinity", acc_infinity.val, global_steps)
                writer.add_scalar(
                    "train_acc_anatomical", acc_anatomical.val, global_steps
                )
                writer.add_scalar("train_acc_coco", acc_coco.val, global_steps)
            else:
                writer.add_scalar("train_acc", acc.val, global_steps)
            writer_dict["train_global_steps"] = global_steps + 1

            prefix = "{}_{}".format(os.path.join(output_dir, "train"), i)
            save_debug_images(config, input, meta, target, pred * 4, output, prefix)


def validate(
    config,
    val_loader,
    val_dataset,
    model,
    criterion,
    output_dir,
    tb_log_dir,
    writer_dict=None,
):
    batch_time = AverageMeter()
    losses = AverageMeter()
    if config.MODEL.NUM_JOINTS == 53:
        acc_infinity = AverageMeter()
        acc_anatomical = AverageMeter()
        acc_coco = AverageMeter()
    else:
        acc = AverageMeter()

    # switch to evaluate mode
    model.eval()

    num_samples = len(val_dataset)
    all_preds = np.zeros((num_samples, 17, 3), dtype=np.float32)
    # all_preds = np.zeros((num_samples, config.MODEL.NUM_JOINTS, 3), dtype=np.float32)
    all_boxes = np.zeros((num_samples, 6))
    image_path = []
    filenames = []
    imgnums = []
    idx = 0
    with torch.no_grad():
        end = time.time()
        for i, (input, target, target_weight, meta) in enumerate(val_loader):
            # compute output
            # outputs = model(input)
            outputs = model(input)[:, :17, :, :]
            if isinstance(outputs, list):
                output = outputs[-1]
            else:
                output = outputs

            if config.TEST.FLIP_TEST:
                input_flipped = input.flip(3)
                outputs_flipped = model(input_flipped)[:, :17, :, :]
                # outputs_flipped = model(input_flipped)

                if isinstance(outputs_flipped, list):
                    output_flipped = outputs_flipped[-1]
                else:
                    output_flipped = outputs_flipped

                output_flipped = flip_back(
                    output_flipped.cpu().numpy(), val_dataset.flip_pairs
                )
                output_flipped = torch.from_numpy(output_flipped.copy()).cuda()

                # feature is not aligned, shift flipped heatmap for higher accuracy
                if config.TEST.SHIFT_HEATMAP:
                    output_flipped[:, :, :, 1:] = output_flipped.clone()[:, :, :, 0:-1]

                output = (output + output_flipped) * 0.5

            target = target.cuda(non_blocking=True)
            target_weight = target_weight.cuda(non_blocking=True)

            loss = criterion(output, target, target_weight)

            num_images = input.size(0)
            # measure accuracy and record loss
            losses.update(loss.item(), num_images)
            if config.MODEL.NUM_JOINTS == 53:
                (
                    (_, avg_acc_infinity, cnt_infinity),
                    (_, avg_acc_anatomical, cnt_anatomical),
                    (_, avg_acc_coco, cnt_coco),
                    pred,
                ) = accuracy_infinity_coco(output.cpu().numpy(), target.cpu().numpy())
                acc_infinity.update(avg_acc_infinity, cnt_infinity)
                acc_anatomical.update(avg_acc_anatomical, cnt_anatomical)
                acc_coco.update(avg_acc_coco, cnt_coco)
            else:
                _, avg_acc, cnt, pred = accuracy(
                    output.cpu().numpy(), target.cpu().numpy()
                )
                acc.update(avg_acc, cnt)

            # measure elapsed time
            batch_time.update(time.time() - end)
            end = time.time()

            c = meta["center"].numpy()
            s = meta["scale"].numpy()
            score = meta["score"].numpy()

            preds, maxvals = get_final_preds(config, output.clone().cpu().numpy(), c, s)

            all_preds[idx : idx + num_images, :, 0:2] = preds[:, :, 0:2]
            all_preds[idx : idx + num_images, :, 2:3] = maxvals
            # double check this all_boxes parts
            all_boxes[idx : idx + num_images, 0:2] = c[:, 0:2]
            all_boxes[idx : idx + num_images, 2:4] = s[:, 0:2]
            all_boxes[idx : idx + num_images, 4] = np.prod(s * 200, 1)
            all_boxes[idx : idx + num_images, 5] = score
            image_path.extend(meta["image"])

            idx += num_images

            if i % config.PRINT_FREQ == 0:
                if config.MODEL.NUM_JOINTS == 53:
                    msg = (
                        "Test: [{0}/{1}]\t"
                        "Time {batch_time.val:.3f} ({batch_time.avg:.3f})\t"
                        "Loss {loss.val:.4f} ({loss.avg:.4f})\t"
                        "Accuracy Infinity {acc_infinity.val:.3f} ({acc_infinity.avg:.3f})\t"
                        "Accuracy Anatomical {acc_anatomical.val:.3f} ({acc_anatomical.avg:.3f})\t"
                        "Accuracy COCO {acc_coco.val:.3f} ({acc_coco.avg:.3f})".format(
                            i,
                            len(val_loader),
                            batch_time=batch_time,
                            loss=losses,
                            acc_infinity=acc_infinity,
                            acc_anatomical=acc_anatomical,
                            acc_coco=acc_coco,
                        )
                    )
                else:
                    msg = (
                        "Test: [{0}/{1}]\t"
                        "Time {batch_time.val:.3f} ({batch_time.avg:.3f})\t"
                        "Loss {loss.val:.4f} ({loss.avg:.4f})\t"
                        "Accuracy {acc.val:.3f} ({acc.avg:.3f})".format(
                            i,
                            len(val_loader),
                            batch_time=batch_time,
                            loss=losses,
                            acc=acc,
                        )
                    )
                logger.info(msg)

                prefix = "{}_{}".format(os.path.join(output_dir, "val"), i)
                save_debug_images(config, input, meta, target, pred * 4, output, prefix)

        name_values, perf_indicator = val_dataset.evaluate(
            config, all_preds, output_dir, all_boxes, image_path, filenames, imgnums
        )

        model_name = config.MODEL.NAME
        if isinstance(name_values, list):
            for name_value in name_values:
                _print_name_value(name_value, model_name)
        else:
            _print_name_value(name_values, model_name)

        if writer_dict:
            writer = writer_dict["writer"]
            global_steps = writer_dict["valid_global_steps"]
            writer.add_scalar("valid_loss", losses.avg, global_steps)
            if config.MODEL.NUM_JOINTS == 53:
                writer.add_scalar("valid_acc_infinity", acc_infinity.avg, global_steps)
                writer.add_scalar(
                    "valid_acc_anatomical", acc_anatomical.avg, global_steps
                )
                writer.add_scalar("valid_acc_coco", acc_coco.avg, global_steps)
            else:
                writer.add_scalar("valid_acc", acc.avg, global_steps)
            if isinstance(name_values, list):
                for name_value in name_values:
                    writer.add_scalars("valid", dict(name_value), global_steps)
            else:
                writer.add_scalars("valid", dict(name_values), global_steps)
            writer_dict["valid_global_steps"] = global_steps + 1

        if config.LOG_WANDB:
            if config.MODEL.NUM_JOINTS == 53:
                wandb.log(
                    {
                        "val/loss_avg": losses.avg,
                        "val/accuracy_infinity_avg": acc_infinity.avg,
                        "val/accuracy_anatomical_avg": acc_anatomical.avg,
                        "val/accuracy_coco_avg": acc_coco.avg,
                    }
                )
            else:
                wandb.log(
                    {
                        "val/loss_avg": losses.avg,
                        "val/accuracy_avg": acc.avg,
                    }
                )

    return perf_indicator


# markdown format output
def _print_name_value(name_value, full_arch_name):
    names = name_value.keys()
    values = name_value.values()
    num_values = len(name_value)
    logger.info("| Arch " + " ".join(["| {}".format(name) for name in names]) + " |")
    logger.info("|---" * (num_values + 1) + "|")

    if len(full_arch_name) > 15:
        full_arch_name = full_arch_name[:8] + "..."
    logger.info(
        "| "
        + full_arch_name
        + " "
        + " ".join(["| {:.3f}".format(value) for value in values])
        + " |"
    )


class AverageMeter(object):
    """Computes and stores the average and current value"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count if self.count != 0 else 0
