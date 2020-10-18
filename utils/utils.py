import torch.nn as nn
import os
import time
import torch
import torchvision
import numpy as np
import cv2 as cv
import json
import scipy.io as sio

def add_view_hm_ori(batch, outputs, phase, epoch, step, log_dir):
    num_img = min(4, batch['input'].shape[0])
    b, c, h, w = batch['line'].shape
    img = torch.from_numpy(
        np.array([cv.resize(im.permute(1, 2, 0).cpu().numpy(), (h, w)) for im in batch['input']])).permute(0, 3, 1,
                                                                                                               2).cuda()
    pic = torchvision.utils.make_grid(img[:num_img], nrow=num_img, padding=2)
    label_center = torchvision.utils.make_grid(batch['center_mask'][:num_img]/5, nrow=num_img, padding=2)
    label_line = torchvision.utils.make_grid(batch['line'][:num_img], nrow=num_img, padding=2)
    mix_tmp = []
    for output in outputs:
        pred_center = torchvision.utils.make_grid(output['center'][:num_img], nrow=num_img, padding=2)
        pred_line = torchvision.utils.make_grid(output['line'][:num_img], nrow=num_img, padding=2)
        mix_tmp.append(torch.cat([pic, label_center, pred_center, label_line, pred_line], dim=1))

    mix_pic = torch.cat([t for t in mix_tmp], dim=2)

    save_dir = log_dir + '/' + str(epoch) + phase
    os.makedirs(save_dir, exist_ok=True)
    cv.imwrite(save_dir + '/' + str(int(step)) + '.png', mix_pic.permute(1, 2, 0).detach().cpu().numpy() * 255.)
    return mix_pic

def load_model(model, model_path, resume=False, selftrain=False):
    start_epoch = 0
    checkpoint = torch.load(model_path, map_location=lambda storage, loc: storage)
    if not selftrain: # True if model is not saved by self-training
        print('loaded', model_path)
        state_dict_ = checkpoint
        state_dict = {}
        for k in state_dict_:
            if k.startswith('module') and not k.startswith('module_list'):
                state_dict[k[7:]] = state_dict_[k]
            else:
                state_dict[k] = state_dict_[k]
        model_state_dict = model.state_dict()

        # check loaded parameters and created modeling parameters
        for k in state_dict:
            if k in model_state_dict:
                if state_dict[k].shape != model_state_dict[k].shape:
                    print('Skip loading parameter {}, required shape{}, ' \
                          'loaded shape{}.'.format(
                        k, model_state_dict[k].shape, state_dict[k].shape))
                    state_dict[k] = model_state_dict[k]
            else:
                print('Drop parameter {}.'.format(k))
        for k in model_state_dict:
            if not (k in state_dict):
                print('No param {}.'.format(k))
                state_dict[k] = model_state_dict[k]
        model.load_state_dict(state_dict, strict=False)
        return model
    else:
        print('loaded {}, epoch {}'.format(model_path, checkpoint['epoch']))
        state_dict_ = checkpoint['state_dict']
        state_dict = {}
        # convert data_parallal to modeling
        for k in state_dict_:
            if k.startswith('module') and not k.startswith('module_list'):
                state_dict[k[7:]] = state_dict_[k]
            else:
                state_dict[k] = state_dict_[k]
        model_state_dict = model.state_dict()

        # check loaded parameters and created modeling parameters
        for k in state_dict:
            if k in model_state_dict:
                if state_dict[k].shape != model_state_dict[k].shape:
                    print('Skip loading parameter {}, required shape{}, ' \
                          'loaded shape{}.'.format(
                        k, model_state_dict[k].shape, state_dict[k].shape))
                    state_dict[k] = model_state_dict[k]
            else:
                print('Drop parameter {}.'.format(k))
        for k in model_state_dict:
            if not (k in state_dict):
                print('No param {}.'.format(k))
                state_dict[k] = model_state_dict[k]
        model.load_state_dict(state_dict, strict=False)

        if resume:
            if 'current_loss' in checkpoint:
                current_loss = checkpoint['current_loss']
                start_epoch = checkpoint['epoch']
                print('current_loss:', current_loss)
                return model, current_loss, start_epoch
            else:
                return model, None, start_epoch
        else:
            return model

def save_model(path, epoch, loss, model):
    if isinstance(model, torch.nn.DataParallel):
        state_dict = model.module.state_dict()
    else:
        state_dict = model.state_dict()
    data = {'epoch': epoch, 'state_dict': state_dict, 'current_loss': loss}
    torch.save(data, path)

def optimizer_define(model, optim_weight, learning_rate):
    backbone_bai, backbone_wei = [], []
    line_bai, line_wei = [], []
    center_bai, center_wei = [], []
    dis_bai, dis_wei = [], []
    for pname, p in model.named_parameters():
        if any([pname.startswith(k) for k in ['resnet', 'up']]):
            if 'bias' in pname or 'bn' in pname:
                backbone_bai += [p]
            else:
                backbone_wei += [p]
        elif any([pname.startswith(k) for k in ['head_l']]):
            if 'bias' in pname or 'bn' in pname:
                line_bai += [p]
            else:
                line_wei += [p]
        elif any([pname.startswith(k) for k in ['head_c', 'line_conv']]):
            if 'bias' in pname or 'bn' in pname:
                center_bai += [p]
            else:
                center_wei += [p]
        elif any([pname.startswith(k) for k in ['head_d', 'center_conv']]):
            if 'bias' in pname or 'bn' in pname:
                dis_bai += [p]
            else:
                dis_wei += [p]
        else:
            print(pname)

    optimizer = torch.optim.Adam([
        {'params': backbone_wei, 'lr': optim_weight['back'] * learning_rate, 'weight_decay': 1e-5},
        {'params': backbone_bai, 'lr': optim_weight['back'] * learning_rate, 'weight_decay': 0},
        {'params': line_wei, 'lr': optim_weight['line'] * learning_rate, 'weight_decay': 1e-5},
        {'params': line_bai, 'lr': optim_weight['line'] * learning_rate, 'weight_decay': 0},
        {'params': center_wei, 'lr': optim_weight['center'] * learning_rate, 'weight_decay': 1e-5},
        {'params': center_bai, 'lr': optim_weight['center'] * learning_rate, 'weight_decay': 0},
        {'params': dis_wei, 'lr': optim_weight['dis'] * learning_rate, 'weight_decay': 1e-5},
        {'params': dis_bai, 'lr': optim_weight['dis'] * learning_rate, 'weight_decay': 0},
    ]
    )
    return optimizer

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
        if self.count > 0:
            self.avg = self.sum / self.count


