import cv2
import torch
import numpy as np
from torchvision import datasets, transforms
from torch.utils.data import DataLoader, TensorDataset


def get_mnist(args, data_dir='./data/mnist/'):
    train = datasets.MNIST(root=data_dir, train=True, download=True)
    test = datasets.MNIST(root=data_dir, train=False, download=True)

    x_unsup = train.data.float().view(-1,784)/255.
    y_unsup = train.targets
    dataloader_unsup = DataLoader(TensorDataset(x_unsup, y_unsup), batch_size=args.batch_size, 
                              shuffle=True, num_workers=0)

    x_sup, y_sup, _ = get_labeled_samples(x_unsup, y_unsup, args.n_shots)
    dataloader_sup = DataLoader(TensorDataset(x_sup, y_sup), batch_size=args.n_shots*10, 
                              shuffle=True, num_workers=0)
    
    x_test = test.data.float().view(-1,784)/255.
    y_test = test.targets
    dataloader_test = DataLoader(TensorDataset(x_test, y_test), batch_size=args.batch_size, 
                              shuffle=True, num_workers=0)

    return dataloader_sup, dataloader_unsup, dataloader_test


def get_webcam(args, data_dir='./data/office31/webcam/images/'):
    data = datasets.ImageFolder(data_dir)

    x = np.array([np.array(data[i][0]) for i in range(len(data))])
    y = np.array([data[i][1] for i in range(len(data))])
    
    x_sup, y_sup, ixs = get_labeled_samples(x, y, args.n_shots)
    data_sup = CaffeTransform(x_sup, y_sup, train=True)
    dataloader_sup = DataLoader(data_sup, batch_size=args.n_shots*31, 
                                shuffle=True, num_workers=0)

    x_unsup, y_unsup = np.delete(x, ixs), np.delete(y, ixs)
    data_unsup = CaffeTransform(x_unsup, y_unsup, train=True)
    dataloader_unsup = DataLoader(data_unsup, batch_size=args.n_shots*31, 
                                  shuffle=True, num_workers=0)

    data_test = CaffeTransform(x_unsup, y_unsup, train=False)
    dataloader_test = DataLoader(data_test, batch_size=args.batch_size, 
                                shuffle=False, num_workers=0)
    return dataloader_sup, dataloader_unsup, dataloader_test


def get_labeled_samples(X, y, n_samples):
    np.random.seed(14)

    classes = np.unique(y)
    indxs = [np.where(y == class_) for class_ in classes]

    ix = []
    for indx in indxs:
        ix.extend(np.random.choice(indx[0], n_samples, replace = False))

    np.random.shuffle(ix)
    X_sup = X[ix]
    y_sup = y[ix]

    return X_sup, y_sup, ix


class CaffeTransform(torch.utils.data.Dataset):

    def __init__(self, X, y, train=False):
        super(CaffeTransform, self).__init__()
        self.X = X
        self.y = y
        self.mean_color = [104.0069879317889, 116.66876761696767, 122.6789143406786]  # BGR 
        self.train = train
        self.output_size = [227, 227]
        if self.train:
            self.horizontal_flip = True
            self.multi_scale = [256, 256]
        else:
            self.horizontal_flip = False
            self.multi_scale = [256, 256]

    def __len__(self):
        return self.X.shape[0]

    def __getitem__(self, idx):
        img, target = self.X[idx], self.y[idx]
        # Flip image at random if flag is selected
        if self.horizontal_flip and np.random.random() < 0.5:
            img = cv2.flip(img, 1)

        if self.multi_scale is None:
            # Resize the image for output
            img = cv2.resize(img, (self.output_size[0], self.output_size[1]))
            img = img.astype(np.float32)
        elif isinstance(self.multi_scale, list):
            # Resize to random scale
            new_size = self.multi_scale[0]

            img = cv2.resize(img, (new_size, new_size))
            img = img.astype(np.float32)
            if new_size != self.output_size[0]:
                if self.train:
                    # random crop at output size
                    diff_size = new_size - self.output_size[0]
                    random_offset_x = np.random.randint(0, diff_size, 1)[0]
                    random_offset_y = np.random.randint(0, diff_size, 1)[0]
                    img = img[random_offset_x:(random_offset_x + self.output_size[0]), random_offset_y:(
                        random_offset_y + self.output_size[0])]
                else:
                    y, x, _ = img.shape
                    startx = x // 2 - self.output_size[0] // 2
                    starty = y // 2 - self.output_size[1] // 2
                    img = img[starty:starty + self.output_size[0], startx:startx + self.output_size[1]]
        img -= np.array(self.mean_color)
        img = torch.from_numpy(img)
        img = img.transpose(0, 1).transpose(0, 2).contiguous()
        return img, target
