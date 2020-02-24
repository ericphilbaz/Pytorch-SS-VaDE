import math
import torch
import numpy as np
from torch import optim
from itertools import cycle
import torch.nn.functional as F
from sklearn.mixture import GaussianMixture


from forward_step import ComputeLosses

def weights_init_normal(m):
    classname = m.__class__.__name__
    if classname.find("Linear") != -1:
        torch.nn.init.normal_(m.weight.data, 0.0, 0.02)

class TrainerVaDE:
    """This is the trainer for the Variational Deep Embedding (VaDE).
    """
    def __init__(self, args, device, dataloader_sup, dataloader_unsup, n_classes):
        if args.dataset == 'mnist':
            from models import Autoencoder, VaDE
            self.autoencoder = Autoencoder().to(device)
            self.autoencoder.apply(weights_init_normal)
            self.VaDE = VaDE().to(device)
        elif args.dataset == 'webcam':
            from models_office import Autoencoder, VaDE, feature_extractor
            self.autoencoder = Autoencoder().to(device)
            checkpoint = torch.load('weights/imagenet_params.pth.tar',
                                    map_location=device)
            self.autoencoder.load_state_dict(checkpoint['state_dict'], strict=False)
            checkpoint = torch.load('weights/feature_extractor_params.pth.tar',
                                     map_location=device)
            self.feature_extractor = feature_extractor().to(device)
            self.feature_extractor.load_state_dict(checkpoint['state_dict'])
            self.freeze_extractor()
            self.VaDE = VaDE().to(device)

        self.dataloader_sup = dataloader_sup
        self.dataloader_unsup = dataloader_unsup
        self.device = device
        self.args = args
        self.n_classes = n_classes


    def pretrain(self):
        """Here we train an stacked autoencoder which will be used as the initialization for the VaDE. 
        This initialization is usefull because reconstruction in VAEs would be weak at the begining
        and the models are likely to get stuck in local minima.
        """
        optimizer = optim.Adam(self.autoencoder.parameters(), lr=0.002)
        self.autoencoder.train()
        print('Training the autoencoder...')
        for epoch in range(1500):
            total_loss = 0
            for x, _ in self.dataloader_unsup:
                optimizer.zero_grad()
                x = x.to(self.device)
                if self.args.dataset == 'webcam':
                    x = self.feature_extractor(x)
                    x = x.detach()
                x_hat = self.autoencoder(x)
                loss = F.mse_loss(x_hat, x, reduction='mean') #reconstruction error
                loss.backward()
                optimizer.step()
                total_loss += loss.item()
            print('Training Autoencoder... Epoch: {}, Loss: {}'.format(epoch, total_loss))
        self.save_weights_ae()
        self.train_GMM() #training a GMM for initialize the VaDE
        self.predict_GMM() #Predict and assign supervised points to its GMM components
        self.save_weights_for_VaDE() #saving weights for the VaDE


    def train_GMM(self):
        """It is possible to fit a Gaussian Mixture Model (GMM) using the latent space 
        generated by the stacked autoencoder. This way, we generate an initialization for 
        the priors (pi, mu, var) of the VaDE model.
        """
        print('Fiting Gaussian Mixture Model...')
        x = torch.cat([data[0] for data in self.dataloader_unsup]).to(self.device) #all x samples.
        if self.args.dataset == 'webcam':
            x = self.feature_extractor(x)
            x = x.detach()
        z = self.autoencoder.encode(x)
        self.gmm = GaussianMixture(n_components=self.n_classes, covariance_type='diag')
        self.gmm.fit(z.cpu().detach().numpy())


    def predict_GMM(self):
        """It is possible to fit a Gaussian Mixture Model (GMM) using the latent space 
        generated by the stacked autoencoder. This way, we generate an initialization for 
        the priors (pi, mu, var) of the VaDE model.
        """
        print('Predicting over Gaussian Mixture Model...')
        x = torch.cat([data[0] for data in self.dataloader_sup]).to(self.device) #all x samples.
        y = torch.cat([data[1] for data in self.dataloader_sup]).to(self.device)
        x = x[np.argsort(y)]
        if self.args.dataset == 'webcam':
            x = self.feature_extractor(x)
            x = x.detach()
        z = self.autoencoder.encode(x)
        probas = self.gmm.predict_proba(z)
        self.assign_GMMS(probas)
    
    def assign_GMMS(self, probas):
        assignation = []
        possibilities = np.arange(self.n_classes)
        index = 0
        toselect = 1
        while len(possibilities)>0:
            sorted_ = np.argsort(probas[index])
            max_ = sorted_[-toselect]
            if max_ in possibilities:
                assignation.append(max_)
                nums = np.setdiff1d(nums, max_)
                index+=1
                toselect=1
            else:
                toselect+=1
        self.assignation = assignation

    def save_weights_for_VaDE(self):
        """Saving the pretrained weights for the encoder, decoder, pi, mu, var.
        """
        print('Saving weights.')
        state_dict = self.autoencoder.state_dict()

        self.VaDE.load_state_dict(state_dict, strict=False)
        self.VaDE.pi_prior.data = torch.from_numpy(self.gmm.weights_[self.assignation]
                                                  ).float().to(self.device)
        self.VaDE.mu_prior.data = torch.from_numpy(self.gmm.means_[self.assignation]
                                                  ).float().to(self.device)
        self.VaDE.log_var_prior.data = torch.log(torch.from_numpy(self.gmm.covariances_[self.assignation]
                                                )).float().to(self.device)
        torch.save(self.VaDE.state_dict(), self.args.pretrained_path)    

    def save_weights_ae(self):
        """Saving the pretrained weights for the encoder, decoder, pi, mu, var.
        """
        print('Saving weights.')
        state = {'state_dict': self.autoencoder.state_dict()}
        torch.save(state, 'weights/autoencoder_parameters.pth.tar') 

    def train(self):
        """
        """
        if self.args.pretrain==True:
            self.VaDE.load_state_dict(torch.load(self.args.pretrained_path,
                                                 map_location=self.device))
        else:
            self.VaDE.apply(weights_init_normal)
        self.optimizer = optim.Adam(self.VaDE.parameters(), lr=self.args.lr)
        lr_scheduler = torch.optim.lr_scheduler.StepLR(
                    self.optimizer, step_size=10, gamma=0.9)

        self.forward_step = ComputeLosses(self.VaDE, self.args)
        print('Training VaDE...')
        for epoch in range(self.args.epochs):
            self.train_VaDE(epoch)
            self.test_VaDE(epoch)
            lr_scheduler.step()


    def train_VaDE(self, epoch):
        self.VaDE.train()
        total_loss = 0
        for (x_s, y_s), (x_u, _) in zip(cycle(self.dataloader_sup), self.dataloader_unsup):
            self.optimizer.zero_grad()
            x_s, y_s = x_s.to(self.device), y_s.to(self.device)
            x_u = x_u.to(self.device)
            if self.args.dataset == 'webcam':
                x_s = self.feature_extractor(x_s)
                x_s = x_s.detach()
                x_u = self.feature_extractor(x_u)
                x_u = x_u.detach()

            loss, reconst_loss, kl_div, acc = self.forward_step.forward('train', x_s, y_s, x_u)
            loss.backward()
            self.optimizer.step()
            total_loss += loss.item()
        print('Training VaDE... Epoch: {}, Loss: {}, Acc: {}'.format(epoch, 
            total_loss/len(self.dataloader_unsup), acc))


    def test_VaDE(self, epoch):
        self.VaDE.eval()
        with torch.no_grad():
            total_loss = 0
            total_acc = 0
            for x, y in self.dataloader_unsup:
                x = x.to(self.device)
                if self.args.dataset == 'webcam':
                    x = self.feature_extractor(x)
                    x = x.detach()
                loss, reconst_loss, kl_div, acc = self.forward_step.forward('test', x, y)
                total_loss += loss.item()
                total_acc += acc.item()
        print('Testing VaDE... Epoch: {}, Loss: {}, Acc: {}'.format(epoch, 
                total_loss/len(self.dataloader_unsup), total_acc/len(self.dataloader_unsup)))

    def freeze_extractor(self):
        for _, param in self.feature_extractor.named_parameters():
            param.requires_grad = False
        self.feature_extractor.eval()
