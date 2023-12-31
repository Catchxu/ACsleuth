import torch.nn as nn
from typing import Optional

from ._block import MemoryBlock, StyleBlock


class LinearBlock(nn.Module):
    def __init__(self, in_dim, out_dim,
                 norm: bool = True, act: bool = True, dropout: bool = True):
        super().__init__()
        self.linear = nn.Sequential(
            nn.Linear(in_dim, out_dim),
            nn.InstanceNorm1d(out_dim) if norm else nn.Identity(),
            nn.LeakyReLU(0.2, inplace=True) if act else nn.Identity(),
            nn.Dropout(0.1) if dropout else nn.Identity(),
        )

    def forward(self, x):
        return self.linear(x)


class ResBlock(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.fc = nn.Sequential(
            LinearBlock(dim, dim),
            LinearBlock(dim, dim, False, False, False)
        )
        self.act = nn.LeakyReLU(0.2, inplace=True)
    
    def forward(self, x):
        return self.act(x + self.fc(x))


class SCNet(nn.Module):
    def __init__(self, in_dim, hidden_dim=[512, 256], n_Res=2):
        super().__init__()

        # Encoder layers
        encoder_layers = []
        layers = [in_dim] + hidden_dim
        dim_1 = layers[0]
        for dim_2 in layers[1:]:
            encoder_layers.append(LinearBlock(dim_1, dim_2))
            dim_1 = dim_2
        encoder_layers.append(nn.Sequential(*[ResBlock(dim_2) for _ in range(n_Res)]))
        self.Encoder = nn.Sequential(*encoder_layers)

        # Decoder layers
        decoder_layers = []
        layers = layers[::-1]
        dim_1 = layers[0]
        decoder_layers.append(nn.Sequential(*[ResBlock(dim_1) for _ in range(n_Res)]))
        for dim_2 in layers[1:]:
            if dim_2 != layers[-1]:
                decoder_layers.append(LinearBlock(dim_1, dim_2))
            else:
                # the last layer don't have norm, act & dropout
                decoder_layers.append(LinearBlock(dim_1, dim_2, False, False, False))
            dim_1 = dim_2

        decoder_layers.append(nn.ReLU())
        self.Decoder = nn.Sequential(*decoder_layers)

        # Additional initialization
        self._init_weights()

    def _init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_normal_(m.weight)


class GeneratorAD(SCNet):
    def __init__(self, in_dim, hidden_dim=[512, 256], n_Res=2,
                 mem_dim=512, threshold=0.01, temperature=1):
        """
        Initialize the GeneratorAD.

        Parameters
        ----------
        in_dim : int
            Input dimension.
        hidden_dim : list of int, optional
            List of hidden layer dimensions.
        n_Res : int, optional
            Number of residual blocks.
        mem_dim : int, optional
            Dimension of the memory block.
        threshold : float, optional
            Threshold for the memory block.
        temperature : float, optional
            Temperature for the memory block.
        """
        super().__init__(in_dim, hidden_dim, n_Res)
        self.Memory = MemoryBlock(mem_dim, hidden_dim[-1], threshold, temperature)

    def forward(self, x):
        """
        Forward pass of the generator for training.

        Parameters
        ----------
        x : torch.Tensor
            Input data.

        Returns
        -------
        x : torch.Tensor
            Output data.
        z : torch.Tensor
            Latent representation.
        """
        z = self.Encoder(x)
        x = self.Decoder(self.Memory(z))
        return x, z
    
    def prepare(self, x):
        """
        Prepare for inference by encoding the input.

        Parameters
        ----------
        x : torch.Tensor
            Input data.

        Returns
        -------
        x : torch.Tensor
            Output data.
        z : torch.Tensor
            Latent representation.
        """
        z = self.Encoder(x)
        x = self.Decoder(z)
        return x, z


class GeneratorDA(SCNet):
    def __init__(self, n_batch, in_dim, hidden_dim=[512, 256], n_Res=2):
        """
        Initialize the GeneratorDA.

        Parameters
        ----------
        n_batch : int
            Batch size.
        in_dim : int
            Input dimension.
        hidden_dim : list of int, optional
            List of hidden layer dimensions.
        n_Res : int, optional
            Number of residual blocks.
        """
        super().__init__(in_dim, hidden_dim, n_Res)
        self.Style = StyleBlock(n_batch, hidden_dim[-1])

    def forward(self, x):
        """
        Forward pass of the generator for training.

        Parameters
        ----------
        x : torch.Tensor
            Input data.

        Returns
        -------
        x : torch.Tensor
            Output data.
        """
        z = self.Encoder(x)
        x = self.Decoder(self.Style(z))
        return x