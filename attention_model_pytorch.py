import unicodedata
import torch
import numpy as np
import random
import pandas as pd
import re
import string
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
SOS_token=0
EOS_token=1
class Lang:
        def __init__(self,name):
                self.name=name
                self.word2index={}
                self.word2count={}
                self.index2word={0:'SOS',1:'EOS'}
                self.n_words=2
        def addSentences(self,sentence):
                for word in sentence.split(' '):
                        self.addWord(word)
        def addWord(self,word):
                if word not in self.word2index:
                        self.word2index[word]=self.n_words
                        self.word2count[word]=1
                        self.index2word[self.n_words]=word
                        self.n_words+=1
                else:
                        self.word2count[word]+=1
def unicodeToAscii(s):
        return ''.join(
                c for c in unicodedata.normalize('NFD',s)
                if unicodedata.category(c)!='Mn'
                )
def normalizeString(s):
        s=unicodeToAscii(s.lower().strip())
        s = re.sub(r"([.!?])", r" \1", s)
        s = re.sub(r"[^a-zA-Z.!?]+", r" ", s)
        return s
def readLangs(lang1,lang2,reverse=False):
        print('Reading Lines..')
        lines=open('data/%s-%s.txt' % (lang1,lang2),encoding='utf-8').\
            read().strip().split('\n')
        pairs=[[normalizeString(s) for s in l.split('\t')] for l in lines]
        if reverse:
                pairs = [list(reversed(p)) for p in pairs]
                input_lang=Lang(lang2)
                output_lang=Lang(lang1)
        else:
                input_lang=Lang(lang1)
                output_lang=Lang(lang2)
        return input_lang,output_lang,pairs
MAX_LENGTH=10
eng_prefixes = (
    "i am ", "i m ",
    "he is", "he s ",
    "she is", "she s ",
    "you are", "you re ",
    "we are", "we re ",
    "they are", "they re "
)
def filterPair(p):
        return len(p[0].split(' ')) <MAX_LENGTH and \
            len(p[1].split(' ')) <MAX_LENGTH and \
            p[1].startswith(eng_prefixes)
def filterPairs(pairs):
        return [pair for pair in pairs if filterPair(pair)]
def prepareData(lang1,lang2,reverse=False):
        input_lang,output_lang,pairs=readLangs(lang1,lang2,reverse)
        print('read %s sentences pairs' % len(pairs))
        pairs = filterPairs(pairs)
        print("Trimmed to %s sentence pairs" % len(pairs))
        print('counting words...')
        for pair in pairs:
                input_lang.addSentences(pair[0])
                output_lang.addSentences(pair[1])
        print('counting words:')
        print(input_lang.name,input_lang.n_words)
        print(output_lang.name,output_lang.n_words)
        return input_lang,output_lang,pairs
input_lang,output_lang,pairs=prepareData('eng','fra',True)
print(random.choice(pairs))
class EncoderRNN(nn.Module):
        def __init__(self,input_size,hidden_size):
                super(EncoderRNN,self).__init__()
                self.hidden_size=hidden_size
                self.embedding=nn.Embedding(input_size,hidden_size)
                self.gru=nn.GRU(hidden_size,hidden_size)
        def forward(self,input,hidden):
                embedded=self.embedding(input).view(1,1,-1)
                output=embedded
                output,hidden=self.gru(output,hidden)
                return output,hidden
        def initHidden(self):
                return  torch.zeros(1,1,self.hidden_size)
class DecoderRNN(nn.Module):
        def __init__(self,hidden_size,output_size):
                super(DecoderRNN,self).__init__()
                self.embedding=nn.Embedding(output_size,hidden_size)
                self.gru=nn.GRU(hidden_size,output_size)
                self.softmax=nn.LogSoftmax(dim=1)
        def forward(self,input,hidden):
                output=self.embedding(input).view(1,1,-1)
                output=F.relu(output)
                output,hidden=self.gru(output,hidden)
                output=self.softmax(self.out(output[0]))
                return output,hidden
        def initHidden(self):
                return torch.zeros(1,1,self,hidden_size)
class AttnDecoderRNN(nn.Module):
        def __init__(self,hidden_size,output_size,dropout_p=0.1,max_length=MAX_LENGTH):
                super(AttnDecoderRNN,self).__init__()
                self.hidden_size=hidden_size
                self.output_size=output_size
                self.dropout_p=dropout_p
                self.max_length=max_length
                self.embedding=nn.Embedding(self.output_size,self.hidden_size)
                self.attn=nn.Linear(self.hidden_size*2,self.max_length)
                self.attn_combine=nn.Linear(self.hidden_size*2,self.hidden_size)
                self.dropout=nn.Dropout(self.dropout_p)
                self.gru=nn.GRU(self.hidden_size,self.hidden_size)
                self.out=nn.Linear(self.hidden_size,self.output_size)
        def forward(self,input,hidden,encoder_outputs):
                embedded=self.embedding(input).view(1,1,-1)
                embedded=self.dropout(embedded)
                attn_weights=F.softmax(
                        self.attn(torch.cat((embedded[0],hidden[0]),1)),dim=1)
                attn_applied=torch.bmm(attn_weights.unsqueeze(0),
                                       encoder_outputs.unsqueeze(0))
                output=torch.cat((embedded[0],attn_applied[0]),1)
                output=self.attn_combine(output).unsqueeze(0)
                output=F.relu(output)
                output,hidden=self.gru(output,hidden)
                output=F.log_softmax(self.out(output[0]),dim=1)
                return output,hidden,attn_weights
        def initHidden(self):
                return torch.zeros(1,1,self.hidden_size)
def indexesFromSentence(lang,sentence):
        return [lang.word2index[word] for word in sentence.split(' ')]
def tensorFromSentence(lang,sentence):
        indexes=indexesFromSentence(lang,sentence)
        indexes.append(EOS_token)
        return torch.tensor(indexes,dtype=torch.long).view(-1,1)
def tensorFromPair(pair):
        input_tensor=tensorFromSentence(input_lang,pair[0])
        target_tensor=tensorFromSentence(output_lang,pair[1])
        return (input_tensor,target_tensor)
teacher_forcing_ratio=0.5
def train(input_tensor,target_tensor,encoder,decoder,encoder_optimizer,decoder_optimizer,criterion,max_length=MAX_LENGTH):
        encoder_hidden=encoder.initHidden()
        encoder_optimizer.zero_grad()
        decoder_optimizer.zero_grad()
        input_length=input_tensor.size(0)
        target_length=target_tensor.size(0)
        encoder_outputs=torch.zeros(max_length,encoder.hidden_size)
        loss=0
        for ei in range(input_length):
                encoder_output,encoder_hidden=encoder(input_tensor[ei]
                ,encoder_hidden)
                encoder_outputs[ei]=encoder_output[0,0]
        decoder_input=torch.tensor([[SOS_token]])
        decoder_hidden=encoder_hidden
        use_teacher_forcing=True if random.random()<teacher_forcing_ratio else False
        if use_teacher_forcing:
                for di in range(target_length):
                        decoder_output,decoder_hidden,decoder_attention=decoder(
                                decoder_input,decoder_hidden,encoder_outputs)
                        loss+=criterion(decoder_output,target_tensor[di])
                        decoder_input=target_tensor[di]
        else:
                for di in range(target_length):
                        decoder_output,decoder_hidden,decoder_attention=decoder(decoder_input,decoder_hidden,encoder_outputs)
                        topv,topi=decoder_output.topk(1)
                        decoder_input=topi.squeeze().detach()
                        loss+=criterion(decoder_output,target_tensor[di])
                        if decoder_input.item()==EOS_token:
                                break
        loss.backward()
        encoder_optimizer.step()
        decoder_optimizer.step()
        return loss.item()/target_length
import time
import math
def asMinutes(s):
    m=math.floor(s/60)
    s-=m*60
    return '%dm %ds' % (m, s)
def timeSince(since,percent):
    now=time.time()
    s=now-since
    es=s/(percent)
    rs=es-s
    return '%s(-%s)'%(asMinutes(s),asMinutes(rs))
def trainIters(encoder,decoder,n_iters,print_every=1000,plot_every=100,learning_rate=0.01):
        start=time.time()
        plot_losses=[]
        print_loss_total=0
        plot_loss_total=0
        encoder_optimizer=optim.SGD(encoder.parameters(),lr=learning_rate)
        decoder_optimizer=optim.SGD(decoder.parameters(),lr=learning_rate)
        training_pairs=[tensorFromPair(random.choice(pairs)) for i in range(n_iters)]
        criterion=nn.NLLLoss()
        for iter in range(1,n_iters+1):
                training_pair=training_pairs[iter-1]
                input_tensor=training_pair[0]
                target_tensor=training_pair[1]
                loss=train(input_tensor,target_tensor,encoder,decoder,encoder_optimizer,decoder_optimizer,criterion)
                print_loss_total+=loss
                plot_loss_total+=loss
                if iter%print_every==0:
                        print_loss_avg=print_loss_total/print_every
                        print_loss_total=0
                        print('%s (%d %d%%) % %4f' % (timeSince(start,iter/n_iters),
                                   iter,iter/n_iters*100,print_loss_avg))
                if iter%plot_every==0:
                        plot_loss_avg=plot_loss_total/plot_every
                        plot_losses.append(plot_loss_avg)
                        plot_loss_total=0
        showPlot(plot_losses)
import matplotlib.pyplot as plt
plt.switch_backend('agg')
import matplotlib.ticker as ticker
def showPlot(points):
    plt.figure()
    fig, ax = plt.subplots()
    loc = ticker.MultipleLocator(base=0.2)
    ax.yaxis.set_major_locator(loc)
    plt.plot(points)
hidden_size=256
encoder1=EncoderRNN(input_lang.n_words,hidden_size)
attn_decoder1=AttnDecoderRNN(hidden_size,output_lang.n_words,dropout_p=0.1)
trainIters(encoder1,attn_decoder1,7500,print_every=5000)