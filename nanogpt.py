# -*- coding: utf-8 -*-
"""nanoGPT

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1xezQbCkPFX25uzCfw6Ik8-XDCh6Hs5ro
"""

# !wget https://raw.githubusercontent.com/karpathy/char-rnn/refs/heads/master/data/tinyshakespeare/input.txt

# load text
with open("input.txt") as file:
    text = file.read()

print(text[:500])

import torch
import torch.nn as nn
import torch.nn.functional as F

torch.manual_seed(42)

# preprocess text
text = text.lower()

# build vocab
chars = sorted(list(set(text)))

batch_size = 64  # number of instances to process simultaneously
block_size = 256  # maximum context length allowed
n_embed = 384
n_heads = 6
head_size = n_embed // n_heads
n_layers = 6
dropout = 0.2
vocab_size = len(chars)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
learning_rate = 2e-4

# define decoder & encoder
stoi = {s: i for i, s in enumerate(chars)}
itos = {i: s for i, s in enumerate(chars)}
encode = lambda text: [stoi[s] for s in text]
decode = lambda toks: "".join([itos[i] for i in toks])

import torch

# encode text
data = torch.tensor(encode(text), dtype=torch.long)
data.shape

# split data
train_set_size = 0.9
n = int(train_set_size * len(data))
train_data = data[:n]
val_data = data[n:]


def get_batch(split):
    if split not in ("train", "val"):
        raise Exception("split must be train or val")

    data = train_data if split == "train" else val_data
    high = len(data) - block_size
    idxs = torch.randint(low=0, high=high, size=(batch_size,))
    x = torch.stack([data[idx : idx + block_size] for idx in idxs])
    y = torch.stack([data[idx + 1 : idx + block_size + 1] for idx in idxs])

    return x.to(device), y.to(device)


# evaluate model performance
@torch.no_grad()
def estimate_loss(model, eval_iters=1_000):
    out = {}
    model.eval()
    for split in ["train", "val"]:
        losses = torch.zeros(eval_iters)
        for i in range(eval_iters):
            xb, yb = get_batch(split)
            _, loss = model(xb, yb)
            losses[i] = loss.item()
        out[split] = losses.mean().item()
    model.train()
    return out


class SingleHeadAttention(nn.Module):
    def __init__(self, n_embed=n_embed, head_size=head_size, dropout=dropout) -> None:
        super().__init__()
        self.key = nn.Linear(n_embed, head_size, bias=False)
        self.query = nn.Linear(n_embed, head_size, bias=False)
        self.value = nn.Linear(n_embed, head_size, bias=False)
        self.register_buffer("tril", torch.tril(torch.ones(block_size, block_size)))
        self.dropout = nn.Dropout()

    def forward(self, x):
        _, T, C = x.shape
        k = self.key(x)  # (B,T,C)
        q = self.query(x)  # (B,T,C)
        weights = q @ k.transpose(-2, -1) * C**-0.5  # (B,T,C) @ (B,C,T) -> (B,T,T)
        weights = weights.masked_fill(self.tril[:T, :T] == 0, float("-inf"))  # (B,T,T)
        weights = F.softmax(weights, dim=-1)  # (B,T,T)
        weights = self.dropout(weights)
        v = self.value(x)  # (B,T,C)
        out = weights @ v  # (B,T,T) @ (B,T,C) -> (B,T,C)
        return out


class MultiHeadAttention(nn.Module):
    def __init__(self, n_heads=n_heads, head_size=head_size, dropout=dropout) -> None:
        super().__init__()
        self.heads = nn.ModuleList([SingleHeadAttention() for _ in range(n_heads)])
        self.proj = nn.Linear(n_embed, n_embed)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        x = torch.cat([h(x) for h in self.heads], dim=-1)
        x = self.proj(x)
        out = self.dropout(x)
        return out


class FeedForwardLayer(nn.Module):
    def __init__(self, dropout=dropout) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_embed, 4 * n_embed),
            nn.ReLU(),
            nn.Linear(4 * n_embed, n_embed),
            nn.Dropout(dropout),
        )

    def forward(self, x):
        return self.net(x)


class Block(nn.Module):
    def __init__(self, n_embed=n_embed, n_heads=n_heads):
        super().__init__()
        self.sa_heads = MultiHeadAttention(n_heads, head_size)
        self.feed_forward = FeedForwardLayer()
        self.ln1 = nn.LayerNorm(n_embed)
        self.ln2 = nn.LayerNorm(n_embed)

    def forward(self, x):
        x = x + self.sa_heads(self.ln1(x))
        x = x + self.feed_forward(self.ln2(x))
        return x


class GPTLM(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.tok_embedding = nn.Embedding(vocab_size, n_embed)
        self.position_embedding = nn.Embedding(block_size, n_embed)
        self.blocks = nn.Sequential(*[Block() for _ in range(n_layers)])
        self.layer_norm = nn.LayerNorm(n_embed)
        self.lm_head = nn.Linear(n_embed, vocab_size)

        # for stabilizing training
        self.apply(self._init_weights)

    def _init_weights(self, module):
        if isinstance(module, nn.Linear):
            torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                torch.nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def forward(self, idx, targets=None):
        B, T = idx.shape
        tok_embed = self.tok_embedding(idx)  # (B,T,C)
        pos_embed = self.position_embedding(torch.arange(T, device=device))  # (T,C)
        x = tok_embed + pos_embed  # (B,T,C)
        x = self.blocks(x)  # (B,T,C)
        x = self.layer_norm(x)  # (B,T,C)
        logits = self.lm_head(x)  # idx: (B, T), logits: (B, T, C)
        B, T, C = logits.shape

        if targets is None:
            return logits, None

        loss = F.cross_entropy(logits.view(B * T, C), targets.view(B * T))
        return logits, loss

    def generate(self, idx, max_new_tokens):
        for _ in range(max_new_tokens):
            idx_cond = idx[:, -block_size:]
            logits, loss = self(idx_cond)
            logits = logits[:, -1, :]  # (B,C)
            probs = F.softmax(logits, dim=-1)  # (B,C)
            idx_next = torch.multinomial(probs, num_samples=1)  # (B,1)
            idx = torch.cat((idx, idx_next), dim=1)  # (B,T+1)
        return idx


model = GPTLM().to(device)
xb, yb = get_batch("train")
logits, loss = model(xb, yb)

decode(
    model.generate(
        torch.zeros((1, 1), dtype=torch.long).to(device), max_new_tokens=100
    )[0].tolist()
)

optim = torch.optim.Adam(model.parameters(), lr=learning_rate)

epochs = 3_000
for e in range(epochs):
    xb, yb = get_batch("train")

    logits, loss = model(xb, yb)
    optim.zero_grad()
    loss.backward()
    optim.step()

    if e % 1_000 == 0:
        print(loss.item())

estimate_loss(model, eval_iters=50)

res = decode(
    model.generate(
        torch.zeros((1, 1), dtype=torch.long).to(device), max_new_tokens=3000
    )[0].tolist()
)
print(res)

with open("out.txt", "w") as file:
    file.write(res)

import torch

torch.save(model.state_dict(), "model_weights.pth")
