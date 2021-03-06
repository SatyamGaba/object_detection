import torch
from torch.autograd import Variable
import torch.functional as F
import dataLoader
import argparse
import torchvision.transforms as transforms
import torchvision.utils as vutils
import torch.optim as optim
from torch.utils.data import DataLoader
import model
import torch.nn as nn
import os
import numpy as np
import utils
import scipy.io as io


parser = argparse.ArgumentParser()
# The location of training set
parser.add_argument('--imageRoot', default='/datasets/cse152-252-sp20-public/hw3_data/VOCdevkit/VOC2012/JPEGImages', help='path to input images' )
parser.add_argument('--labelRoot', default='/datasets/cse152-252-sp20-public/hw3_data/VOCdevkit/VOC2012/SegmentationClass', help='path to input images' )
parser.add_argument('--trainFileList', default='/datasets/cse152-252-sp20-public/hw3_data/VOCdevkit/VOC2012/ImageSets/Segmentation/train.txt', help='path to input images' )
parser.add_argument('--valFileList', default='/datasets/cse152-252-sp20-public/hw3_data/VOCdevkit/VOC2012/ImageSets/Segmentation/trainval.txt', help='path to input images' )
parser.add_argument('--experiment', default='checkpoint', help='the path to store sampled images and models')
parser.add_argument('--imHeight', type=int, default=300, help='height of input image')
parser.add_argument('--imWidth', type=int, default=300, help='width of input image')
parser.add_argument('--batchSize', type=int, default=16, help='the size of a batch')
parser.add_argument('--numClasses', type=int, default=21, help='the number of classes' )
parser.add_argument('--nepoch', type=int, default=100, help='the training epoch')
parser.add_argument('--initLR', type=float, default=0.1, help='the initial learning rate')
parser.add_argument('--noCuda', action='store_true', help='do not use cuda for training')
parser.add_argument('--gpuId', type=int, default=0, help='gpu id used for training the network')
parser.add_argument('--isDilation', action='store_true', help='whether to use dialated model or not' )
parser.add_argument('--isSpp', action='store_true', help='whether to do spatial pyramid or not' )
parser.add_argument('--untrainedResnet', action='store_false', help='whether to train resnet block from scratch or load pretrained weights' )

# The detail network setting
opt = parser.parse_args()
print(opt)

if opt.isSpp == True :
    opt.isDilation = False

if opt.isDilation:
    opt.experiment += '_dilation'
    opt.modelRoot += '_dilation'
if opt.isSpp:
    opt.experiment += '_spp'
    opt.modelRoot += '_spp'

# Save all the codes
os.system('mkdir %s' % opt.experiment )
os.system('cp *.py %s' % opt.experiment )

if torch.cuda.is_available() and opt.noCuda:
    print("WARNING: You have a CUDA device, so you should probably run with --cuda")

# Initialize network
if opt.isDilation:
    encoder = model.encoderDilation()
    decoder = model.decoderDilation()
elif opt.isSpp:
    encoder = model.encoderSPP()
    decoder = model.decoderSPP()
else:
    encoder = model.encoder()
    decoder = model.decoder()
    
if opt.untrainedResnet:
    # load pretrained weights for resBlock
    model.loadPretrainedWeight(encoder)
    
# Move network and containers to gpu
if not opt.noCuda:
    device = 'cuda'
else:
    device = 'cpu'
    
encoder = encoder.to(device)
decoder = decoder.to(device)

# Initialize optimizer
params = list(encoder.parameters()) + list(decoder.parameters())
optimizer = optim.SGD(params, lr=opt.initLR, momentum=0.9, weight_decay=5e-4 )

#augment the dataset with transformations
transformations = [
    transforms.RandomCrop(320),
    transforms.RandomRotation((0,90)),
    transforms.RandomHorizontalFlip(0.5),
    transforms.RandomVerticalFlip(0.5)
]
tfs = transforms.Compose(transformations)

# Initialize dataLoader
segTrainDataset = dataLoader.BatchLoader(
        imageRoot = opt.imageRoot,
        labelRoot = opt.labelRoot,
        fileList = opt.trainFileList,
        imWidth = opt.imWidth,
        imHeight = opt.imHeight,
        # transforms = tfs
        )
segValDataset = dataLoader.BatchLoader(
        imageRoot = opt.imageRoot,
        labelRoot = opt.labelRoot,
        fileList = opt.valFileList,
        imWidth = opt.imWidth,
        imHeight = opt.imHeight,
        # transforms = tfs
        )
segTrainLoader = DataLoader(segTrainDataset, batch_size=opt.batchSize, num_workers=0, shuffle=True )
segValLoader = DataLoader(segValDataset, batch_size=opt.batchSize, num_workers=0, shuffle=True )
   
trainLossArr = []
valLossArr = []
trainAccuracyArr = []
valAccuracyArr = []

trainLossArrEpoch = []
valLossArrEpoch = []
trainAccuracyArrEpoch = []
valAccuracyArrEpoch = []

train_iteration = 0
val_iteration = 0


def train():
    """Training Code"""
    global train_iteration
    epoch = opt.nepoch
    confcounts = np.zeros( (opt.numClasses, opt.numClasses), dtype=np.int64 )
    accuracy = np.zeros(opt.numClasses, dtype=np.float32 )

    for epoch in range(0, opt.nepoch ):
        encoder.train()
        decoder.train()
        
        running_acc = []
        running_losses = []
        
        trainingLog = open('{0}/trainingLog_{1}.txt'.format(opt.experiment, epoch), 'w')
        for i, dataBatch in enumerate(segTrainLoader ):
            train_iteration += 1

            # Read data
            imBatch = Variable(dataBatch['im']).to(device)
            labelBatch = Variable(dataBatch['label']).to(device)
            labelIndexBatch = Variable(dataBatch['labelIndex']).to(device)
            maskBatch = Variable(dataBatch['mask']).to(device)

            # Train network
            optimizer.zero_grad()

            x1, x2, x3, x4, x5 = encoder(imBatch )
            pred = decoder(imBatch, x1, x2, x3, x4, x5 )
            
            # cross-entropy loss
            loss = torch.mean( pred * labelBatch )
            hist = utils.computeAccuracy(pred, labelIndexBatch, maskBatch )
            confcounts += hist

            for n in range(0, opt.numClasses ):
                rowSum = np.sum(confcounts[n, :] )
                colSum = np.sum(confcounts[:, n] )
                interSum = confcounts[n, n]
                accuracy[n] = float(100.0 * interSum) / max(float(rowSum + colSum - interSum ), 1e-5)
            
            loss.backward()

            optimizer.step()

            # Output the log information
            trainLossArr.append(loss.cpu().data.item() )
            meanLoss = np.mean(np.array(trainLossArr[:] ) )
            trainMeanAccuracy = np.mean(accuracy )
            trainAccuracyArr.append(trainMeanAccuracy)
            running_losses.append(loss.cpu().data.item() )
            running_acc.append(accuracy)

            
            if train_iteration >= 100:
                meanLoss = np.mean(np.array(trainLossArr[-100:] ) )
                trainMeanAccuracy = np.mean(np.array(trainAccuracyArr[-100:] ) )
            else:
                meanLoss = np.mean(np.array(trainLossArr[:] ) )
                trainMeanAccuracy = np.mean(np.array(trainAccuracyArr[:] ) )

            print('Train:- Epoch %d iteration %d: Loss %.5f Accumulated Loss %.5f' % (epoch, train_iteration, trainLossArr[-1], meanLoss ) )
            print('Train:- Epoch %d iteration %d: Accura %.5f Accumulated Accura %.5f' % (epoch, train_iteration, trainAccuracyArr[-1], trainMeanAccuracy ) )
            trainingLog.write('Epoch %d iteration %d: Loss %.5f Accumulated Loss %.5f \n' % (epoch, train_iteration, trainLossArr[-1], meanLoss ) )
            trainingLog.write('Epoch %d iteration %d: Accura %.5f Accumulated Accura %.5f\n' % (epoch, train_iteration, trainAccuracyArr[-1], trainMeanAccuracy ) )

        trainingLog.close()
        
        trainLossArrEpoch.append( np.mean(np.array(running_losses)) )
        trainAccuracyArrEpoch.append( np.mean(np.array(running_acc)) )
        
        #save per epoch readings
        np.save('%s/train_epoch_loss.npy' % opt.experiment, np.array(trainLossArrEpoch ) )
        np.save('%s/train_epoch_accuracy.npy' % opt.experiment, np.array(trainAccuracyArrEpoch ) )
        
        
        if (epoch+1) % 2 == 0:
            np.save('%s/train_loss.npy' % opt.experiment, np.array(trainLossArr ) )
            np.save('%s/train_accuracy.npy' % opt.experiment, np.array(trainAccuracyArr ) )
            torch.save(encoder.state_dict(), '%s/encoder_%d.pth' % (opt.experiment, epoch+1) )
            torch.save(decoder.state_dict(), '%s/decoder_%d.pth' % (opt.experiment, epoch+1) )

        val(epoch)

def val(epoch):
    """Validation Code"""
    global val_iteration
    encoder.eval()
    decoder.eval()

    running_acc = []
    running_losses = []
    
    confcounts = np.zeros( (opt.numClasses, opt.numClasses), dtype=np.int64 )
    accuracy = np.zeros(opt.numClasses, dtype=np.float32 )

    validLog = open('{0}/valLog_{1}.txt'.format(opt.experiment, epoch), 'w')
    for i, dataBatch in enumerate(segValLoader ):
        val_iteration += 1

        # Read data
        imBatch = Variable(dataBatch['im']).to(device)
        labelBatch = Variable(dataBatch['label']).to(device)
        labelIndexBatch = Variable(dataBatch['labelIndex']).to(device)
        maskBatch = Variable(dataBatch['mask']).to(device)

        x1, x2, x3, x4, x5 = encoder(imBatch )
        pred = decoder(imBatch, x1, x2, x3, x4, x5 )
        
        # cross-entropy loss
        loss = torch.mean( pred * labelBatch )
        hist = utils.computeAccuracy(pred, labelIndexBatch, maskBatch )
        confcounts += hist

        for n in range(0, opt.numClasses ):
            rowSum = np.sum(confcounts[n, :] )
            colSum = np.sum(confcounts[:, n] )
            interSum = confcounts[n, n]
            accuracy[n] = float(100.0 * interSum) / max(float(rowSum + colSum - interSum ), 1e-5)

        # Output the log information
        valLossArr.append(loss.cpu().data.item() )
        meanLoss = np.mean(np.array(valLossArr[:] ) )
        valMeanAccuracy = np.mean(accuracy )
        valAccuracyArr.append(valMeanAccuracy)
        running_losses.append(loss.cpu().data.item() )
        running_acc.append(accuracy)
        
        if val_iteration >= 100:
            meanLoss = np.mean(np.array(valLossArr[-100:] ) )
            valMeanAccuracy = np.mean(np.array(valAccuracyArr[-100:] ) )
        else:
            meanLoss = np.mean(np.array(valLossArr[:] ) )
            valMeanAccuracy = np.mean(np.array(valAccuracyArr[:] ) )

        print('Val:- Epoch %d iteration %d: Loss %.5f Accumulated Loss %.5f' % (epoch, val_iteration, valLossArr[-1], meanLoss ) )
        print('Val:- Epoch %d iteration %d: Accura %.5f Accumulated Accura %.5f' % (epoch, val_iteration, valAccuracyArr[-1], valMeanAccuracy ) )
        validLog.write('Epoch %d iteration %d: Loss %.5f Accumulated Loss %.5f \n' % (epoch, val_iteration, valLossArr[-1], meanLoss ) )
        validLog.write('Epoch %d iteration %d: Accura %.5f Accumulated Accura %.5f\n' % (epoch, val_iteration, valAccuracyArr[-1], valMeanAccuracy ) )

    validLog.close()
    
    valLossArrEpoch.append( np.mean(np.array(running_losses)) )
    valAccuracyArrEpoch.append( np.mean(np.array(running_acc)) )
    
    #save per epoch readings
    np.save('%s/val_epoch_loss.npy' % opt.experiment, np.array(valLossArrEpoch ) )
    np.save('%s/val_epoch_accuracy.npy' % opt.experiment, np.array(valAccuracyArrEpoch ) )
    
    if (epoch+1) % 2 == 0:
        np.save('%s/val_loss.npy' % opt.experiment, np.array(valLossArr ) )
        np.save('%s/val_accuracy.npy' % opt.experiment, np.array(valAccuracyArr ) )
        torch.save(encoder.state_dict(), '%s/encoder_%d.pth' % (opt.experiment, epoch+1) )
        torch.save(decoder.state_dict(), '%s/decoder_%d.pth' % (opt.experiment, epoch+1) )
        
        
train()