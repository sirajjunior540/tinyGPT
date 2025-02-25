import torch.optim
import torch 
import torch.nn as nn
from torch.nn import functional as F
import requests


batch_size = 64 # how many independent sequences will we process in parallel
block_size  = 256 # what is the maximum context length for predictions?
max_iters =  5000
eval_intervals = 500
learning_rate = 3e-4
device = "cuda" if torch.cuda.is_available() else 'cpu'
eval_iters = 200
n_embed = 384
n_head = 6
n_layer = 6
dropout = 0.2

torch.manual_seed(1337)
# response = requests.get('https://raw.githubusercontent.com/karpathy/char-rnn/master/data/tinyshakespeare/input.txt')

# with open('input.txt', 'w', encoding='utf-8') as wr:
#     wr.write(response.text)

with open("input.txt", 'r', encoding='utf-8') as f:
    text = f.read()

chars = sorted(list(set(text)))
vocab_size = len(chars)


stoi = { ch:i  for i, ch in enumerate(chars)}
itos = { i:ch for i, ch in enumerate(chars)}
encode = lambda s: [stoi[c] for c in s] # list of numbers
decode = lambda l: "".join([itos[i] for i in l]) # strings


data = torch.tensor(encode(text), dtype=torch.long)
n = int(0.9*len(data))
train_data = data[:n]
val_data =  data[n:]


def get_batch(split):
    data = train_data if split == 'train' else val_data
    ix = torch.randint(len(data) - block_size, (batch_size,))
    x = torch.stack([data[i:i+block_size] for i in ix])
    y = torch.stack([data[i+1:i+block_size+1] for i in ix])
    return x, y


@torch.no_grad() #  everything happen here we will not call backward at all
def estimate_loss():
    out = {}
    model.eval()
    for split in ['train', 'val']:
        losses = torch.zeros(eval_iters)
        for k in range(eval_iters):
            X, Y = get_batch(split)
            logits , loss = model(X, Y)
            losses[k]= loss.item()
        out[split] = losses.mean()
    model.train()
    return out

class Block(nn.Module):
    def __init__(self, n_embed, n_head):
        super().__init__()
        head_size = n_embed
        self.sa = MultiHeadAttention(n_head, head_size)
        self.ffwd = FeedForward(n_embed)
        self.ln1 = nn.LayerNorm(n_embed)
        self.ln2 = nn.LayerNorm(n_embed)

    def forward(self, x):
        x = x + self.sa(self.ln1(x))
        x = x + self.ffwd(self.ln2(x))
        return x

class FeedForward(nn.Module):
    def __init__(self, n_embed):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_embed, 4*n_embed), nn.ReLU(),nn.Linear(4*n_embed, n_embed), # instead of doing self.proj
            nn.LayerNorm(n_embed),
            nn.Dropout(dropout)
        )
    
    def forward(self, x):
        return self.net(x)

class BigramLanguageModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.token_embedding_table = nn.Embedding(vocab_size, n_embed)
        self.position_embedding_table = nn.Embedding(block_size, n_embed)
        self.lm_head = nn.Linear(n_embed, vocab_size)
        # self.sa_head = MultiHeadAttention(4,n_embed//4) # four heads of 8 dimentional self attention
        # self.ffwd = FeedForward(n_embed)
        self.blocks = nn.Sequential( *[Block(n_embed, n_head=n_head) for _ in range(n_layer)] # in another way
            # Block(n_embed, n_head=4),
            # Block(n_embed, n_head=4),
            # Block(n_embed, n_head=4),
        )

    def forward(self, idx, targets=None): # tragets us B.T 
        B, T = idx.shape
        tok_emb = self.token_embedding_table(idx) # (B.T.C) which means Batch by Time by Channel tensor
        pos_emb = self.position_embedding_table(torch.arange(T, device=device)) # T. C
        x = tok_emb + pos_emb
        x = self.blocks(x)
        # x = self.sa_head(x)
        # x = self.ffwd(x)
        logits = self.lm_head(x) # (B.T.vocab_size) 
        if targets is None:
            loss = None
        else:
          B, T, C = logits.shape
          logits = logits.view(B*T, C)
          targets = targets.view(B*T)
          loss = F.cross_entropy(logits, targets)
        return logits, loss

    def generate(self, idx, max_new_tokens):
      # idx is (B, T) array of indices in the current context
        for _ in range(max_new_tokens):
            idx_cond = idx[:, -block_size:]
          # get the predictions
            logits, loss = self(idx_cond)
            # focus only on the last time step
            logits = logits[:, -1, :] # become (B, C)
            # apply softmax to get probabilities
            probs = F.softmax(logits, dim=-1) # become (B, C)
            # sample from the distribution
            idx_next = torch.multinomial(probs, num_samples=1) # (B, 1)
            # append sampled index to the running sequence
            idx = torch.cat((idx, idx_next), dim=1) # (B, T+1)
        return idx
    
class Head(nn.Module):
    def __init__(self, head_size):
        super().__init__()
        self.key = nn.Linear(n_embed, head_size, bias=False)
        self.query = nn.Linear(n_embed, head_size, bias=False)
        self.value = nn.Linear(n_embed, head_size, bias=False)
        self.register_buffer('tril', torch.tril(torch.ones(block_size, block_size)))

        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        B, T, C = x.shape
        k = self.key(x)
        q =  self.query(x)

        wei =  q @ k.transpose(-2, -1)*C**-0.5
        wei = wei.masked_fill(self.tril[:T, :T]==0, float('-inf'))
        wei = F.softmax(wei, dim=-1)

        wei = self.dropout(wei)

        v = self.value(x)
        out = wei @ v
        return out

class MultiHeadAttention(nn.Module):
    def __init__(self, num_heads, head_size):
        super().__init__()
        self.heads = nn.ModuleList([Head(head_size) for _ in  range(num_heads)])
        self.proj = nn.Linear(num_heads * head_size , n_embed)
        self.dropout = nn.Dropout(dropout)
    
    def forward(self, x):
        out =  torch.cat([h(x) for h in self.heads], dim=-1)
        out = self.dropout(self.proj(out))

        return out
model = BigramLanguageModel()

m = model.to(device)

optimizer = torch.optim.AdamW(model.parameters(), lr = learning_rate)

for item in range(max_iters):
    if item % eval_intervals == 0:
        losses = estimate_loss()
        print(f'stop {iter}: train loss {losses['train']:.4f}, val loss {losses['val']:.4f}')

    xb, yb = get_batch('train')

    logits, loss = model(xb, yb)
    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    optimizer.step()


context = torch.zeros((1,1), dtype=torch.long, device=device)
print(decode(m.generate(context, max_new_tokens=500)[0].tolist()))