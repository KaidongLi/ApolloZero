import numpy as np
import torch
import torch.nn as nn

def calc_iou(a, b):
    area = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])

    iw = torch.min(torch.unsqueeze(a[:, 2], dim=1), b[:, 2]) - torch.max(torch.unsqueeze(a[:, 0], 1), b[:, 0])
    ih = torch.min(torch.unsqueeze(a[:, 3], dim=1), b[:, 3]) - torch.max(torch.unsqueeze(a[:, 1], 1), b[:, 1])

    iw = torch.clamp(iw, min=0)
    ih = torch.clamp(ih, min=0)

    ua = torch.unsqueeze((a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1]), dim=1) + area - iw * ih

    ua = torch.clamp(ua, min=1e-8)

    intersection = iw * ih

    IoU = intersection / ua

    return IoU

class FocalLoss(nn.Module):
    #def __init__(self):

    #def forward(self, classifications, regressions, anchors, annotations):
    def forward(self, classifications, regressions, locscores, anchors, annotations, regBox, clipBox, imgs):       # wenchi
        alpha = 0.25
        gamma = 2.0
        batch_size = classifications.shape[0]
        classification_losses = []
        regression_losses = []
        locscore_losses = []

        anchor = anchors[0, :, :]

        anchor_widths  = anchor[:, 2] - anchor[:, 0]
        anchor_heights = anchor[:, 3] - anchor[:, 1]
        anchor_ctr_x   = anchor[:, 0] + 0.5 * anchor_widths
        anchor_ctr_y   = anchor[:, 1] + 0.5 * anchor_heights

        for j in range(batch_size):

            classification = classifications[j, :, :]
            regression = regressions[j, :, :]
            locscore = locscores[j, :, :]

            bbox_annotation = annotations[j, :, :]
            bbox_annotation = bbox_annotation[bbox_annotation[:, 4] != -1]

            if bbox_annotation.shape[0] == 0:
                regression_losses.append(torch.tensor(0).float().cuda())
                classification_losses.append(torch.tensor(0).float().cuda())
                locscore_losses.append(torch.tensor(0).float().cuda())

                continue

            classification = torch.clamp(classification, 1e-4, 1.0 - 1e-4)

            IoU = calc_iou(anchors[0, :, :], bbox_annotation[:, :4]) # num_anchors x num_annotations

            IoU_max, IoU_argmax = torch.max(IoU, dim=1) # num_anchors x 1

            #import pdb
            #pdb.set_trace()

            # compute the loss for classification
            targets = torch.ones(classification.shape) * -1
            targets = targets.cuda()

            targets[torch.lt(IoU_max, 0.4), :] = 0

            positive_indices = torch.ge(IoU_max, 0.5)

            num_positive_anchors = positive_indices.sum()

            assigned_annotations = bbox_annotation[IoU_argmax, :]

            targets[positive_indices, :] = 0
            targets[positive_indices, assigned_annotations[positive_indices, 4].long()] = 1

            alpha_factor = torch.ones(targets.shape).cuda() * alpha

            alpha_factor = torch.where(torch.eq(targets, 1.), alpha_factor, 1. - alpha_factor)
            focal_weight = torch.where(torch.eq(targets, 1.), 1. - classification, classification)
            focal_weight = alpha_factor * torch.pow(focal_weight, gamma)

            bce = -(targets * torch.log(classification) + (1.0 - targets) * torch.log(1.0 - classification))

            # cls_loss = focal_weight * torch.pow(bce, gamma)
            cls_loss = focal_weight * bce

            cls_loss = torch.where(torch.ne(targets, -1.0), cls_loss, torch.zeros(cls_loss.shape).cuda())

            classification_losses.append(cls_loss.sum()/torch.clamp(num_positive_anchors.float(), min=1.0))



            # compute the loss for localization score          # wenchi, kaidong
            #print('loss', 'max sc', torch.max(locscore))
            #locscore = torch.clamp( locscore[positive_indices, :] , 1e-4, 1.0 - 1e-4)
            #locscore = locscore[positive_indices, :]
            #locscore = torch.clamp(locscore, 1e-4, 1.0 - 1e-4)
            #print('loss', 'af clmp', locscore)
            # for test, kaidong
            #print('loss', 'locscore', locscore[52:55])
            #IoU_max = IoU_max[positive_indices]
            #IoU_max = IoU_max.contiguous().view(IoU_max.shape[0], -1)

            #locscore = torch.clamp((1.0 - torch.abs(locscore - IoU_max)), 1e-4, 1.0 - 1e-4)
            #print('loss', 'loc for log', locscore[52:55])
            #locscore_loss = -torch.log( locscore )              # wenchi
            # for test, kaidong
            #print('loss', 'loss', locscore_loss[50:55])
            #locscore_losses.append(locscore_loss.mean())            # wenchi

            # compute the loss for regression

            if positive_indices.sum() > 0:
                assigned_annotations = assigned_annotations[positive_indices, :]

                anchor_widths_pi = anchor_widths[positive_indices]
                anchor_heights_pi = anchor_heights[positive_indices]
                anchor_ctr_x_pi = anchor_ctr_x[positive_indices]
                anchor_ctr_y_pi = anchor_ctr_y[positive_indices]

                gt_widths  = assigned_annotations[:, 2] - assigned_annotations[:, 0]
                gt_heights = assigned_annotations[:, 3] - assigned_annotations[:, 1]
                gt_ctr_x   = assigned_annotations[:, 0] + 0.5 * gt_widths
                gt_ctr_y   = assigned_annotations[:, 1] + 0.5 * gt_heights

                # clip widths to 1
                gt_widths  = torch.clamp(gt_widths, min=1)
                gt_heights = torch.clamp(gt_heights, min=1)

                targets_dx = (gt_ctr_x - anchor_ctr_x_pi) / anchor_widths_pi
                targets_dy = (gt_ctr_y - anchor_ctr_y_pi) / anchor_heights_pi
                targets_dw = torch.log(gt_widths / anchor_widths_pi)
                targets_dh = torch.log(gt_heights / anchor_heights_pi)

                targets = torch.stack((targets_dx, targets_dy, targets_dw, targets_dh))
                targets = targets.t()

                targets = targets/torch.Tensor([[0.1, 0.1, 0.2, 0.2]]).cuda()


                negative_indices = 1 - positive_indices

                regression_diff = torch.abs(targets - regression[positive_indices, :])

                '''
                # for test, kaidong
                print('loss', 'anc shape', anchor.shape)
                print('loss', 'reg shape', regression.shape)
                print('loss', 'reg shape', regression[positive_indices, :].shape)
                print('loss', 'img shape', imgs[j, :, :, :].shape)
                '''

                # modify data dimension, kaidong
                anchor_iou = anchor[positive_indices, :].unsqueeze(0)
                regression_iou = regression[positive_indices, :].unsqueeze(0)
                img_iou = imgs[j, :, :, :].unsqueeze(0)

                '''
                # for test, kaidong
                print('loss', 'anc iou shape', anchor_iou.shape)
                print('loss', 'reg iou shape', regression_iou.shape)
                print('loss', 'img iou shape', img_iou.shape)
                '''


                # calculate prediction boxes, kaidong
                shifted_anchors = regBox(anchor_iou, regression_iou)
                shifted_anchors = clipBox(shifted_anchors, img_iou)

                #'''
                # for test, kaidong
                #print('loss', 'ann shap', assigned_annotations.shape)
                #print('loss', 'anc shap', shifted_anchors.shape)
                #print('loss', 'reg 02', regression_iou[0, 0:3, :])
                #print('loss', 'anc 02', anchor_iou[0, 0:3, :])
                #print('loss', 'sh ac 02', shifted_anchors[0, 0:3, :])
                #print('loss', 'idx ', positive_indices)
                #print('loss', 'idx sum', positive_indices.nonzero())
                #print('loss', 'ann 09', assigned_annotations[0:10, 0:4])
                #'''


                # calculate iou, kaidong
                IoU_shift = calc_iou(shifted_anchors.squeeze(0), assigned_annotations[:, :4])
                IoU_shift_max, IoU_shift_argmax = torch.max(IoU_shift, dim=1)
                #IoU_shift_max = torch.tensor(IoU_shift_max, requires_grad = False)
                IoU_shift_max = IoU_shift_max.detach()

                # make loc score, kaidong
                locscore = locscore[positive_indices, :].squeeze(1)

                # calculate diff, kaidong
                #regression_diff_new = torch.abs(assigned_annotations[:, 0:4] - shifted_anchors.squeeze(0))

                '''
                # for test, kaidong
                print('loss', 'grad?', IoU_shift_max.requires_grad)
                #print('loss', 'iou 02', IoU_shift[0:3, :])
                #print('loss', 'iou m 02', IoU_shift_max[0:3])
                #print('loss', 'lcsc 02', locscore[0:3])
                #print('loss', 'iou shp', IoU_shift_max.shape)
                #print('loss', 'lcsc m shp', locscore.shape)
                #print('loss', 'ori diff', regression_diff)
                #print('loss', 'our diff', regression_diff_new)
                #print('loss', 'ori shp', regression_diff.shape)
                #print('loss', 'our shp', regression_diff_new.shape)
                #print('loss', 'our dif', regression_diff_new[0:3, :])
                '''

                # calculate loss, kaidong
                locscore = torch.clamp((1.0 - torch.abs(locscore - IoU_shift_max)), 1e-4, 1.0 - 1e-4)
                locscore_loss = -torch.log( locscore )
                locscore_losses.append(locscore_loss.mean())

                regression_loss = torch.where(
                    torch.le(regression_diff, 1.0 / 9.0),
                    0.5 * 9.0 * torch.pow(regression_diff, 2),
                    regression_diff - 0.5 / 9.0
                )
                regression_losses.append(regression_loss.mean())
            else:
                regression_losses.append(torch.tensor(0).float().cuda())
                locscore_losses.append(torch.tensor(0).float().cuda())

        #return torch.stack(classification_losses).mean(dim=0, keepdim=True), torch.stack(regression_losses).mean(dim=0, keepdim=True)
        return torch.stack(classification_losses).mean(dim=0, keepdim=True), torch.stack(regression_losses).mean(dim=0, keepdim=True), torch.stack(locscore_losses).mean(dim=0, keepdim=True)      # wenchi
