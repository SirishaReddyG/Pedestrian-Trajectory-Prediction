import copy

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from .relational_multi_attention_forward import relational_multi_head_attention_forward


def get_noise(shape, noise_type):
    if noise_type == "gaussian":
        return torch.randn(shape).cuda()
    elif noise_type == "uniform":
        return torch.rand(*shape).sub_(0.5).mul_(2.0).cuda()
    raise ValueError('Unrecognized noise type "%s"' % noise_type)


def get_subsequent_mask(seq):
    ''' For masking out the subsequent info. '''
    sz_b, len_s = seq.size()
    subsequent_mask = (1 - torch.triu(
        torch.ones((1, len_s, len_s), device=seq.device), diagonal=1)).bool()
    return subsequent_mask


def _get_activation_fn(activation):
    if activation == "relu":
        return F.relu
    elif activation == "gelu":
        return F.gelu
    else:
        raise RuntimeError("activation should be relu/gelu, not %s." % activation)


def _get_clones(module, N):
    return nn.ModuleList([copy.deepcopy(module) for i in range(N)])


class MultiheadAttention(nn.Module):
    r"""Allows the model to jointly attend to information
    from different representation subspaces.
    See reference: Attention Is All You Need
    .. math::
        \text{MultiHead}(Q, K, V) = \text{Concat}(head_1,\dots,head_h)W^O
        \text{where} head_i = \text{Attention}(QW_i^Q, KW_i^K, VW_i^V)
    Args:
        embed_dim: total dimension of the model.
        num_heads: parallel attention heads.
        dropout: a Dropout layer on attn_output_weights. Default: 0.0.
        bias: add bias as module parameter. Default: True.
        add_bias_kv: add bias to the key and value sequences at dim=0.
        add_zero_attn: add a new batch of zeros to the key and
                       value sequences at dim=1.
        kdim: total number of features in key. Default: None.
        vdim: total number of features in key. Default: None.
        Note: if kdim and vdim are None, they will be set to embed_dim such that
        query, key, and value have the same number of features.
    Examples::
        >>> multihead_attn = nn.MultiheadAttention(embed_dim, num_heads)
        >>> attn_output, attn_output_weights = multihead_attn(query, key, value)
    """
    __constants__ = ['q_proj_weight', 'k_proj_weight', 'v_proj_weight', 'in_proj_weight',
                     'q_proj_weight2', 'k_proj_weight2', 'v_proj_weight2', 'in_proj_weight2']

    def __init__(self, embed_dim, num_heads, dropout=0., bias=True, add_bias_kv=False, add_zero_attn=False, kdim=None,
                 vdim=None):
        super(MultiheadAttention, self).__init__()
        self.embed_dim = embed_dim
        self.kdim = kdim if kdim is not None else embed_dim
        self.vdim = vdim if vdim is not None else embed_dim
        self._qkv_same_embed_dim = self.kdim == embed_dim and self.vdim == embed_dim

        self.num_heads = num_heads
        self.dropout = dropout
        self.head_dim = embed_dim // num_heads
        assert self.head_dim * num_heads == self.embed_dim, "embed_dim must be divisible by num_heads"

        if self._qkv_same_embed_dim is False:
            self.q_proj_weight = nn.Parameter(torch.Tensor(embed_dim, embed_dim))
            self.q_proj_weight2 = nn.Parameter(torch.Tensor(embed_dim, embed_dim))
            self.k_proj_weight = nn.Parameter(torch.Tensor(embed_dim, self.kdim))
            self.k_proj_weight2 = nn.Parameter(torch.Tensor(embed_dim, self.kdim))
            self.v_proj_weight = nn.Parameter(torch.Tensor(embed_dim, self.vdim))
            self.v_proj_weight2 = nn.Parameter(torch.Tensor(embed_dim, self.vdim))
            self.register_parameter('in_proj_weight', None)
            self.register_parameter('in_proj_weight2', None)
        else:
            self.in_proj_weight = nn.Parameter(torch.empty(3 * embed_dim, embed_dim))
            self.in_proj_weight2 = nn.Parameter(torch.empty(3 * embed_dim, embed_dim))
            self.register_parameter('q_proj_weight', None)
            self.register_parameter('q_proj_weight2', None)
            self.register_parameter('k_proj_weight', None)
            self.register_parameter('k_proj_weight2', None)
            self.register_parameter('v_proj_weight', None)
            self.register_parameter('v_proj_weight2', None)

        if bias:
            self.in_proj_bias = nn.Parameter(torch.empty(3 * embed_dim))
            self.in_proj_bias2 = nn.Parameter(torch.empty(3 * embed_dim))
        else:
            self.register_parameter('in_proj_bias', None)
            self.register_parameter('in_proj_bias2', None)
        self.out_proj = nn.Linear(embed_dim, embed_dim, bias=bias)
        self.out_proj2 = nn.Linear(embed_dim, embed_dim, bias=bias)

        if add_bias_kv:
            self.bias_k = nn.Parameter(torch.empty(1, 1, embed_dim))
            self.bias_k2 = nn.Parameter(torch.empty(1, 1, embed_dim))
            self.bias_v = nn.Parameter(torch.empty(1, 1, embed_dim))
            self.bias_v2 = nn.Parameter(torch.empty(1, 1, embed_dim))
        else:
            self.bias_k = self.bias_v = None
            self.bias_k2 = self.bias_v2 = None

        self.add_zero_attn = add_zero_attn

        self._reset_parameters()

    def _reset_parameters(self):
        if self._qkv_same_embed_dim:
            nn.init.xavier_uniform_(self.in_proj_weight)
            nn.init.xavier_uniform_(self.in_proj_weight2)
        else:
            nn.init.xavier_uniform_(self.q_proj_weight)
            nn.init.xavier_uniform_(self.q_proj_weight2)
            nn.init.xavier_uniform_(self.k_proj_weight)
            nn.init.xavier_uniform_(self.k_proj_weight2)
            nn.init.xavier_uniform_(self.v_proj_weight)
            nn.init.xavier_uniform_(self.v_proj_weight2)

        if self.in_proj_bias is not None:
            nn.init.constant_(self.in_proj_bias, 0.)
            nn.init.constant_(self.in_proj_bias2, 0.)
            nn.init.constant_(self.out_proj.bias, 0.)
            nn.init.constant_(self.out_proj2.bias, 0.)
        if self.bias_k is not None:
            nn.init.xavier_normal_(self.bias_k)
            nn.init.xavier_normal_(self.bias_k2)
        if self.bias_v is not None:
            nn.init.xavier_normal_(self.bias_v)
            nn.init.xavier_normal_(self.bias_v2)

    def __setstate__(self, state):
        # Support loading old MultiheadAttention checkpoints generated by v1.1.0
        if '_qkv_same_embed_dim' not in state:
            state['_qkv_same_embed_dim'] = True

        super(MultiheadAttention, self).__setstate__(state)

    def forward(self, query, key, value, key_padding_mask=None,
                need_weights=True, attn_mask=None):
        # type: (Tensor, Tensor, Tensor, Optional[Tensor], bool, Optional[Tensor]) -> Tuple[Tensor, Optional[Tensor]]
        r"""
    Args:
        query, key, value: map a query and a set of key-value pairs to an output.
            See "Attention Is All You Need" for more details.
        key_padding_mask: if provided, specified padding elements in the key will
            be ignored by the attention. This is an binary mask. When the value is True,
            the corresponding value on the attention layer will be filled with -inf.
        need_weights: output attn_output_weights.
        attn_mask: mask that prevents attention to certain positions. This is an additive mask
            (i.e. the values will be added to the attention layer).
    Shape:
        - Inputs:
        - query: :math:`(L, N, E)` where L is the target sequence length, N is the batch size, E is
          the embedding dimension.
        - key: :math:`(S, N, E)`, where S is the source sequence length, N is the batch size, E is
          the embedding dimension.
        - value: :math:`(S, N, E)` where S is the source sequence length, N is the batch size, E is
          the embedding dimension.
        - key_padding_mask: :math:`(N, S)`, ByteTensor, where N is the batch size, S is the source sequence length.
        - attn_mask: :math:`(L, S)` where L is the target sequence length, S is the source sequence length.
        - Outputs:
        - attn_output: :math:`(L, N, E)` where L is the target sequence length, N is the batch size,
          E is the embedding dimension.
        - attn_output_weights: :math:`(N, L, S)` where N is the batch size,
          L is the target sequence length, S is the source sequence length.
        """
        if not self._qkv_same_embed_dim:
            return relational_multi_head_attention_forward(
                query, key, value, self.embed_dim, self.num_heads,
                self.in_proj_weight, self.in_proj_weight2,
                self.in_proj_bias, self.in_proj_bias2, 
                self.bias_k, self.bias_k2, self.bias_v, self.bias_v2,
                self.add_zero_attn, self.dropout, self.out_proj.weight,self.out_proj2.weight, self.out_proj.bias,
                self.out_proj2.bias,training=self.training,
                key_padding_mask=key_padding_mask, need_weights=need_weights,
                attn_mask=attn_mask, use_separate_proj_weight=True,
                q_proj_weight=self.q_proj_weight, q_proj_weight2=self.q_proj_weight2, 
                k_proj_weight=self.k_proj_weight, k_proj_weight2=self.k_proj_weight2,
                v_proj_weight=self.v_proj_weight,v_proj_weight2=self.v_proj_weight2)
        else:
            return relational_multi_head_attention_forward(
                query, key, value, self.embed_dim, self.num_heads,
                self.in_proj_weight,self.in_proj_weight2, self.in_proj_bias, self.in_proj_bias2,
                self.bias_k, self.bias_k2,self.bias_v,self.bias_v2,self.add_zero_attn,
                self.dropout, self.out_proj.weight,self.out_proj2.weight, self.out_proj.bias,self.out_proj2.bias,
                training=self.training,
                key_padding_mask=key_padding_mask, need_weights=need_weights,
                attn_mask=attn_mask)


class TransformerEncoderLayer(nn.Module):

    def __init__(self, d_model, nhead, dim_feedforward=2048, dropout=0, activation="relu"):
        super(TransformerEncoderLayer, self).__init__()
        self.self_attn = MultiheadAttention(d_model, nhead, dropout=dropout)
        # Implementation of Feedforward model
        self.linear1 = nn.Linear(d_model, dim_feedforward)
        self.dropout = nn.Dropout(dropout)
        self.linear2 = nn.Linear(dim_feedforward, d_model)

        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.dropout1 = nn.Dropout(dropout)
        self.dropout2 = nn.Dropout(dropout)

        self.activation = _get_activation_fn(activation)

    def forward(self, src, src_mask=None, src_key_padding_mask=None):
        r"""Pass the input through the encoder layer.

        Args:
            src: the sequnce to the encoder layer (required).
            src_mask: the mask for the src sequence (optional).
            src_key_padding_mask: the mask for the src keys per batch (optional).

        Shape:
            see the docs in Transformer class.
        """
        src2, attn = self.self_attn(src, src, src, attn_mask=src_mask,
                                    key_padding_mask=src_key_padding_mask)
        src = src + self.dropout1(src2)
        src = self.norm1(src)

        if hasattr(self, "activation"):
            src2 = self.linear2(self.dropout(self.activation(self.linear1(src))))
        else:  # for backward compatibility
            src2 = self.linear2(self.dropout(F.relu(self.linear1(src))))

        src = src + self.dropout2(src2)
        src = self.norm2(src)
        return src, attn


class TransformerEncoder(nn.Module):
    r"""TransformerEncoder is a stack of N encoder layers

    Args:
        encoder_layer: an instance of the TransformerEncoderLayer() class (required).
        num_layers: the number of sub-encoder-layers in the encoder (required).
        norm: the layer normalization component (optional).

    Examples::
        >>> encoder_layer = nn.TransformerEncoderLayer(d_model=512, nhead=8)
        >>> transformer_encoder = nn.TransformerEncoder(encoder_layer, num_layers=6)
        >>> src = torch.rand(10, 32, 512)
        >>> out = transformer_encoder(src)
    """

    def __init__(self, encoder_layer, num_layers, norm=None):
        super(TransformerEncoder, self).__init__()
        self.layers = _get_clones(encoder_layer, num_layers)
        self.num_layers = num_layers
        self.norm = norm

    def forward(self, src, mask=None, src_key_padding_mask=None):
        r"""Pass the input through the encoder layers in turn.

        Args:
            src: the sequnce to the encoder (required).
            mask: the mask for the src sequence (optional).
            src_key_padding_mask: the mask for the src keys per batch (optional).

        Shape:
            see the docs in Transformer class.
        """
        output = src

        atts = []

        for i in range(self.num_layers):
            output, attn = self.layers[i](output, src_mask=mask,
                                          src_key_padding_mask=src_key_padding_mask)
            atts.append(attn)
        if self.norm:
            output = self.norm(output)

        return output


class TransformerModel(nn.Module):
    

    def __init__(self, ninp, nhead, nhid, nlayers, dropout=0.5):
        
        super(TransformerModel, self).__init__()
        self.model_type = 'Transformer'
        self.src_mask = None
        encoder_layers = TransformerEncoderLayer(ninp, nhead, nhid, dropout)
        self.transformer_encoder = TransformerEncoder(encoder_layers, nlayers)
        self.ninp = ninp
        print("ninp: ",ninp)

    def forward(self, src, mask):
        n_mask = mask + torch.eye(mask.shape[0], mask.shape[0]).cuda()
        n_mask = n_mask.float().masked_fill(n_mask == 0., float(-1e20)).masked_fill(n_mask == 1., float(0.0))
        output = self.transformer_encoder(src, mask=n_mask)

        return output


class tp_model(torch.nn.Module):

    def __init__(self, args, dropout=0.1):
        super(tp_model, self).__init__()

        # set parameters for network architecture
        self.embedding_size = [32]
        self.output_size = 2
        self.dropout_prob = dropout
        self.args = args

        self.temporal_encoder_layer = TransformerEncoderLayer(d_model=32, nhead=8)

        emsize = 32  # embedding dimension
        nhid = 2048  # the dimension of the feedforward network model in TransformerEncoder
        nlayers = 2  # the number of nn.TransformerEncoderLayer in nn.TransformerEncoder
        nhead = 8  # the number of heads in the multihead-attention models
        dropout = 0.1  # the dropout value

        self.spatial_encoder_1 = TransformerModel(emsize, nhead, nhid, nlayers, dropout)
        self.spatial_encoder_2 = TransformerModel(emsize, nhead, nhid, nlayers, dropout)

        self.temporal_encoder_1 = TransformerEncoder(self.temporal_encoder_layer, 1)
        self.temporal_encoder_2 = TransformerEncoder(self.temporal_encoder_layer, 1)

        # Linear layer to map input to embedding
        self.input_embedding_layer_temporal = nn.Linear(2, 32)
        self.input_embedding_layer_spatial = nn.Linear(2, 32)

        # Linear layer to output and fusion
        self.output_layer = nn.Linear(48, 2)
        self.fusion_layer = nn.Linear(64, 32)

        # ReLU and dropout init
        self.relu = nn.ReLU()
        self.dropout_in = nn.Dropout(self.dropout_prob)

    def get_st_ed(self, batch_num):
        """

        :param batch_num: contains number of pedestrians in different scenes for a batch
        :type batch_num: list
        :return: st_ed: list of tuple contains start index and end index of pedestrians in different scenes
        :rtype: list
        """
        cumsum = torch.cumsum(batch_num, dim=0)
        st_ed = []
        for idx in range(1, cumsum.shape[0]):
            st_ed.append((int(cumsum[idx - 1]), int(cumsum[idx])))

        st_ed.insert(0, (0, int(cumsum[0])))

        return st_ed

    def get_node_index(self, seq_list):
        """

        :param seq_list: mask indicates whether pedestrain exists
        :type seq_list: numpy array [F, N], F: number of frames. N: Number of pedestrians (a mask to indicate whether
                                                                                            the pedestrian exists)
        :return: All the pedestrians who exist from the beginning to current frame
        :rtype: numpy array
        """
        for idx, framenum in enumerate(seq_list):

            if idx == 0:
                node_indices = framenum > 0
            else:
                node_indices *= (framenum > 0)

        return node_indices

    def update_batch_pednum(self, batch_pednum, ped_list):
        """

        :param batch_pednum: batch_num: contains number of pedestrians in different scenes for a batch
        :type list
        :param ped_list: mask indicates whether the pedestrian exists through the time window to current frame
        :type tensor
        :return: batch_pednum: contains number of pedestrians in different scenes for a batch after removing pedestrian who disappeared
        :rtype: list
        """
        updated_batch_pednum_ = copy.deepcopy(batch_pednum).cpu().numpy()
        updated_batch_pednum = copy.deepcopy(batch_pednum)

        cumsum = np.cumsum(updated_batch_pednum_)
        new_ped = copy.deepcopy(ped_list).cpu().numpy()

        for idx, num in enumerate(cumsum):
            num = int(num)
            if idx == 0:
                updated_batch_pednum[idx] = len(np.where(new_ped[0:num] == 1)[0])
            else:
                updated_batch_pednum[idx] = len(np.where(new_ped[int(cumsum[idx - 1]):num] == 1)[0])

        return updated_batch_pednum

    def mean_normalize_abs_input(self, node_abs, st_ed):
        """

        :param node_abs: Absolute coordinates of pedestrians
        :type Tensor
        :param st_ed: list of tuple indicates the indices of pedestrians belonging to the same scene
        :type List of tupule
        :return: node_abs: Normalized absolute coordinates of pedestrians
        :rtype: Tensor
        """
        node_abs = node_abs.permute(1, 0, 2)
        for st, ed in st_ed:
            mean_x = torch.mean(node_abs[st:ed, :, 0])
            mean_y = torch.mean(node_abs[st:ed, :, 1])

            node_abs[st:ed, :, 0] = (node_abs[st:ed, :, 0] - mean_x)
            node_abs[st:ed, :, 1] = (node_abs[st:ed, :, 1] - mean_y)

        return node_abs.permute(1, 0, 2)

    def forward(self, inputs, iftest=False):

        nodes_abs, nodes_norm, shift_value, seq_list, nei_lists, nei_num, batch_pednum = inputs
        num_Ped = nodes_norm.shape[1]
        outputs = torch.zeros(nodes_norm.shape[0], num_Ped, 2).cuda()
        GM = torch.zeros(nodes_norm.shape[0], num_Ped, 32).cuda()
        GM_S = torch.zeros(nodes_abs.shape[0], num_Ped, 32).cuda()

        noise = get_noise((1, 16), 'gaussian')

        for framenum in range(self.args.seq_length - 1):

            if framenum >= self.args.obs_length and iftest:

                node_index = self.get_node_index(seq_list[:self.args.obs_length])
                updated_batch_pednum = self.update_batch_pednum(batch_pednum, node_index)
                st_ed = self.get_st_ed(updated_batch_pednum)

                nodes_current = outputs[self.args.obs_length - 1:framenum, node_index]
                nodes_current = torch.cat((nodes_norm[:self.args.obs_length, node_index], nodes_current))
                node_abs_base = nodes_abs[:self.args.obs_length, node_index]
                node_abs_pred = shift_value[self.args.obs_length:framenum + 1, node_index] + outputs[
                                                                                           self.args.obs_length - 1:framenum,
                                                                                           node_index]
                node_abs = torch.cat((node_abs_base, node_abs_pred), dim=0)
                # We normalize the absolute coordinates using the mean value in the same scene
                node_abs = self.mean_normalize_abs_input(node_abs, st_ed)

            else:
                node_index = self.get_node_index(seq_list[:framenum + 1])
                nei_list = nei_lists[framenum, node_index, :]
                nei_list = nei_list[:, node_index]
                updated_batch_pednum = self.update_batch_pednum(batch_pednum, node_index)
                st_ed = self.get_st_ed(updated_batch_pednum)
                nodes_current = nodes_norm[:framenum + 1, node_index]
                # We normalize the absolute coordinates using the mean value in the same scene
                node_abs = self.mean_normalize_abs_input(nodes_abs[:framenum + 1, node_index], st_ed)

            # Input Embedding
            if framenum == 0:
                temporal_input_embedded = self.dropout_in(self.relu(self.input_embedding_layer_temporal(nodes_current)))
                spatial_input_embedded = self.dropout_in(self.relu(self.input_embedding_layer_spatial(node_abs)))
            else:
                temporal_input_embedded = self.dropout_in(self.relu(self.input_embedding_layer_temporal(nodes_current)))
                temporal_input_embedded[:framenum] = GM[:framenum, node_index]
                spatial_input_embedded = self.dropout_in(self.relu(self.input_embedding_layer_spatial(node_abs)))
                spatial_input_embedded[:framenum] = GM_S[:framenum, node_index]

            spatial_input_embedded = self.spatial_encoder_1(spatial_input_embedded[-1].unsqueeze(1), nei_list)

            spatial_input_embedded = spatial_input_embedded.permute(1, 0, 2)[-1]
            temporal_input_embedded_last = self.temporal_encoder_1(temporal_input_embedded)[-1]

            fusion_feat = torch.cat((temporal_input_embedded_last, spatial_input_embedded), dim=1)
            fusion_feat = self.fusion_layer(fusion_feat)

            spatial_input_embedded = self.spatial_encoder_2(fusion_feat.unsqueeze(1), nei_list)
            spatial_input_embedded = spatial_input_embedded.permute(1, 0, 2)
            GM_S[framenum, node_index]=spatial_input_embedded

            temporal_input_embedded = self.temporal_encoder_2(spatial_input_embedded)[-1]

            noise_to_cat = noise.repeat(temporal_input_embedded.shape[0], 1)
            temporal_input_embedded_wnoise = torch.cat((temporal_input_embedded, noise_to_cat), dim=1)
            outputs_current = self.output_layer(temporal_input_embedded_wnoise)
            outputs[framenum, node_index] = outputs_current
            GM[framenum, node_index] = temporal_input_embedded

        return outputs
