import anndata as ad
from tqdm import tqdm
from typing import Optional, Dict

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader

from ._utils import seed_everything
from .model import GeneratorAD, Discriminator, Predictor


class CoarseSleuth:
    def __init__(self,
                 prepare_epochs: int = 20,
                 train_epochs: int = 100,
                 predict_epochs: int = 20,
                 batch_size: int = 256,
                 learning_rate: float = 1e-4,
                 n_critic: int = 1,
                 GPU: bool = True,
                 weight: Optional[Dict[str, float]] = None,
                 random_state: Optional[int] = None):
        self.prepare_epochs = prepare_epochs
        self.train_epochs = train_epochs
        self.predict_epochs = predict_epochs
        self.bs = batch_size
        self.lr = learning_rate
        self.n_critic = n_critic
        self.device = torch.device("cuda:0" if GPU and torch.cuda.is_available() else "cpu")
        self.weight = weight or {'w_rec': 50, 'w_adv': 1, 'w_gp': 10}
        
        if random_state is not None:
            seed_everything(random_state)
    
    def _create_opt_sch(self, model, lr, betas=(0.5, 0.999), T_max=None):
        optimizer = optim.Adam(model.parameters(), lr=lr, betas=betas)
        scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer=optimizer, T_max=T_max) if T_max else None
        return optimizer, scheduler

    def _prepare(self, prepare_epochs):
        with tqdm(total=prepare_epochs) as t:
            for _ in range(prepare_epochs):
                t.set_description(f'Prepare Epochs')

                self._train(prepare=True)

                t.set_postfix(G_Loss = self.G_loss.item(),
                              D_Loss = self.D_loss.item())
                t.update(1)

    def _train(self, prepare):
        for data in self.loader:
            data = data.to(self.device)
            for _ in range(self.n_critic):
                self._update_D(data, prepare)

            self._update_G(data, prepare)

    def _update_D(self, data, prepare):
        fake_data, _ = self.G.prepare(data) if prepare else self.G(data)

        d1 = torch.mean(self.D(data))
        d2 = torch.mean(self.D(fake_data.detach()))
        gp = self.D.gradient_penalty(data, fake_data.detach())
        self.D_loss = - d1 + d2 + gp * self.weight['w_gp']

        self.opt_D.zero_grad()
        self.D_loss.backward()
        self.opt_D.step()
    
    def _update_G(self, data, prepare):
        fake_data, z = self.G.prepare(data) if prepare else self.G(data)

        # discriminator provides feedback
        d = self.D(fake_data)

        L_rec = self.L1(data, fake_data)
        L_adv = -torch.mean(d)
        self.G_loss = self.weight['w_rec']*L_rec + self.weight['w_adv']*L_adv
        self.opt_G.zero_grad()
        self.G_loss.backward()
        self.opt_G.step()

        self.G.Memory.update_mem(z)

    def _check(self, tgt: ad.AnnData):
        if (tgt.var_names != self.genes).any():
            raise RuntimeError('Target and reference data have different genes.')

        if (self.G is None or self.D is None):
            raise RuntimeError('Please train the model first.')

    def detect(self, ref: ad.AnnData):
        tqdm.write('Begin to train ACsleuth on the reference dataset...')

        self.genes = ref.var_names
        train_data = torch.Tensor(ref.X)
        self.loader = DataLoader(train_data, batch_size=self.bs, shuffle=True,
                                 num_workers=4, pin_memory=True, drop_last=True)

        self.D = Discriminator(ref.n_vars).to(self.device)
        self.G = GeneratorAD(ref.n_vars).to(self.device)

        self.opt_D, self.sch_D = self._create_opt_sch(self.D, self.lr, T_max=self.n_epochs)
        self.opt_G, self.sch_G = self._create_opt_sch(self.G, self.lr, T_max=self.n_epochs)
        self.L1 = nn.L1Loss().to(self.device)

        self.D.train()
        self.G.train()

        self._prepare(self.prepare_epochs)

        with tqdm(total=self.train_epochs) as t:
            for _ in range(self.train_epochs):
                t.set_description(f'Train Epochs')

                self._train(prepare=False)

                # Update learning rate for G and D
                self.sch_D.step()
                self.sch_G.step()
                t.set_postfix(G_Loss = self.G_loss.item(),
                              D_Loss = self.D_loss.item())
                t.update(1)
        
        tqdm.write('Training has been finished.')
    
    def predict(self, tgt: ad.AnnData):
        self._check(tgt)

        tqdm.write('Begin to detect anomalies on the target dataset...')
        test_data = torch.Tensor(tgt.X)
        self.loader = DataLoader(test_data, batch_size=self.bs, shuffle=False,
                                 num_workers=4, pin_memory=True, drop_last=False)

        self.D.eval()
        self.G.eval()
        real_d, fake_d = [], []
        
        with torch.no_grad():
            for data in self.loader:
                data = data.to(self.device)
                fake_data = self.G(data)

                real_d.append(self.D(data).detach())
                fake_d.append(self.D(fake_data).detach())

        real_d = torch.cat(real_d, dim=0)
        fake_d = torch.cat(fake_d, dim=0)
        self.P = Predictor(self.D.hidden_dim[-1]).to(self.device)
        self.opt_P, self.sch_P = self._create_opt_sch(self.P, self.lr, T_max=self.n_epochs)

        self.P.train()
        with tqdm(total=self.predict_epochs) as t:
            for _ in range(self.predict_epochs):
                t.set_description(f'Predict Epochs')

                _, loss = self.P(real_d, fake_d)
                self.opt_P.zero_grad()
                loss.backward()
                self.opt_P.step()
                self.sch_P.step()
                t.set_postfix(P_Loss = loss.item())
                t.update(1)
        
        self.P.eval()
        p, _ = self.P(real_d, fake_d)
        tqdm.write('Anomalies have been detected.')
        return p.cpu().detach().numpy()







        
