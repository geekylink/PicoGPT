import os
import sys
import time
import torch
import pickle
import datetime
import argparse
from torch.utils.data import Dataset, DataLoader
from transformers import GPT2LMHeadModel, GPT2Tokenizer, AdamW

VERSION="0.0.1"

START_DELAY=5 # Delay before spinning up your GPU so you can quick confirm settings, set to 0 to skip

class TextDataset(Dataset):
    def __init__(self):
        self.input_ids = []
        self.attn_masks = []

    def tokenize(self, txt_list, tokenizer):
        print("Tokenizing...")
        for txt in txt_list:
            inputs = tokenizer.encode_plus(txt, max_length=512, padding='max_length', truncation=True, return_tensors='pt')
            self.input_ids.append(inputs['input_ids'])
            self.attn_masks.append(inputs['attention_mask'])

    def __len__(self):
        return len(self.input_ids)

    def __getitem__(self, idx):
        return self.input_ids[idx].squeeze(), self.attn_masks[idx].squeeze()

    def save(self, outModel: str = "."):
        print("Saving tokenized dataset to file...")

        if not os.path.isdir(outModel):
            os.mkdir(outModel)

        with open(outModel + "/inputids.bin", "wb") as f:
            pickle.dump(self.input_ids, f)
        with open(outModel + "/attnmasks.bin", "wb") as f:
            pickle.dump(self.attn_masks, f)

        print("Save complete")

    def load(self, inModel: str = "."):
        print("Loading tokenized dataset from file...")
        with open(inModel + "/inputids.bin", "rb") as f:
            self.input_ids = pickle.load(f)
        with open(inModel + "/attnmasks.bin", "rb") as f:
            self.attn_masks = pickle.load(f)
        print("Tokenized data loaded.")
        #self.input_ids = 

def getCudaDevice():
    """
        Return CUDA device
    """
    device = 'cuda' if torch.cuda.is_available() else 'cpu'  # Use 'cuda:0' if you want to specify GPU number

    if torch.cuda.is_available():
        print("Detected Cuda device")
    else:
        print("ERROR: No Cuda device detected.")
        return None

    return device

def loadModel(device = None, inModel: str = ""):
    """
        Load model path if provided, otherwise default to GPT2
    """
    modelPath = inModel if inModel != "" else "gpt2"

    if not os.path.isdir(modelPath):
        print("Loading tokenizer:", modelPath ,"...")
        tokenizer = GPT2Tokenizer.from_pretrained(modelPath)
        tokenizer.pad_token = tokenizer.eos_token
    else:
        tokenizer = None
    print("Loading model", modelPath ,"...")
    model = GPT2LMHeadModel.from_pretrained(modelPath)

    # This moves the model to GPU if available
    if device:
        print("Selecting cuda device...")
        model = model.to(device)  

    return [model, tokenizer]


def loadData(inFile: str, chunkSize = 4096):
    """
        Loads data and chunks it
        Choose chunk size according to your memory constraints
    """
    print("\nLoading input data...")
    txtData = ""

    print("Loading file:", inFile)
    with open(inFile) as f:
        txtData = f.read()

    dataLen = len(txtData)
    dataKB = round(dataLen/1024, 2)
    dataMB = round(dataLen/1024/1024, 2)
    dataGB = round(dataLen/1024/1024/1024, 5)
    dataTB = round(dataLen/1024/1024/1024/1024, 6)

    print("Input data loaded.")
    print("Input length:", dataLen, "bytes")
    print(dataKB, "\tKB")
    print(dataMB, "\tMB")
    if dataMB > 1:
        print(dataGB, "\tGB")
    if dataGB > 0.1:
        print(dataTB, "\tTB")

    print("")
    print("Chunking data into chunks of size:", chunkSize)
    chunks = [txtData[i:i+chunkSize] for i in range(0, len(txtData), chunkSize)]

    return chunks

def tokenize(tokenizer, chunks, outModel: str = "", batchSize: int = 2):
    """
        Tokenizes the data
        Adjust batchSize to fit your GPU
    """
    print("==============================")
    print("Tokenizing data... @", datetime.datetime.now())
    print("==============================")

    dataset = TextDataset()
    dataset.tokenize(chunks, tokenizer)
    dataset.save(outModel)

    dataloader = DataLoader(dataset, batchSize)  

    print("==============================")
    print("Data loaded. @", datetime.datetime.now())
    print("==============================")

    return dataloader

def loadTokenizedData(inModel, batchSize=2):

    print("==============================")
    print("Loading tokenized data...", datetime.datetime.now())
    print("==============================")
    dataset = TextDataset()
    dataset.load(inModel)

    dataloader = DataLoader(dataset, batchSize)  

    print("==============================")
    print("Data loaded. @", datetime.datetime.now())
    print("==============================")

    return dataloader

def saveModel(model = None, tokenizer = None, optimizer = None, outModel: str  = "out/PicoGPT-unnamed.model"):

    print("Saving snapshot of model to:", outModel)

    if model:
        print("saving model...")
        model.save_pretrained(outModel)

    if tokenizer:
        print("Saving tokenizer...")
        tokenizer.save_pretrained(outModel)

    if optimizer:
        print("Saving optimizer and model state...")
        torch.save({
                #'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                }, outModel + "./chk.pt")


def doEpochs(device, model, tokenizer, dataloader, numEpochs: int = 3, snapshots: int = 1, outModel: str = "out/PicoGPT.unnamed.model"):

    # Skip on no epochs
    if numEpochs == 0:
        return

    outEvery = 100 # Output status every 'outEvery' batch
    it = 0

    # TODO: use something else for optimizer
    print("Selecting optimizer...")
    optimizer = AdamW(model.parameters(), lr=2e-5, eps=1e-8)  # Define the optimizer, in this case, AdamW.

    # Restore the model & optimizer state from previous training if available
    if os.path.isfile(outModel + "./chk.pt"):
        print("Restoring model and optimizer state...")
        checkpoint = torch.load(outModel + "./chk.pt")
        #model.load_state_dict(checkpoint['model_state_dict'])
        optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
    else:
        print("No checkpoint file detected.")


    print("Clearing cache...")
    torch.cuda.empty_cache()

    print("")
    #time.sleep(10)
    print("Starting training...")

    gradientAccum = 2
    print("snapshots: ", snapshots)

    for epoch in range(numEpochs):
        it = 0
        totalIt = len(dataloader)

        print("==============================")
        print("Starting Epoch", epoch+1, "of", numEpochs ,"- Batches per epoch:", totalIt, " @", datetime.datetime.now())
        print("==============================")

        # Initialize loss for this epoch
        epochLoss = 0.0

        model.zero_grad()

        for batch in dataloader:
            it += 1

            input_ids, attn_masks = batch
            input_ids = input_ids.to(device)
            attn_masks = attn_masks.to(device)

            # Zero the parameter gradients
            #model.zero_grad()

            outputs = model(input_ids, attention_mask=attn_masks, labels=input_ids)

            # Compute loss
            loss = outputs.loss

            # Accumulate loss for the epoch
            thisLoss = loss.item()
            epochLoss += thisLoss

            # Backward propagation and optimization
            #optimizer.zero_grad()
            loss.backward()

            if it % gradientAccum == 0:
                optimizer.step()
                model.zero_grad()

            # Print status
            if it % outEvery == 0:
                averageLoss = epochLoss / (it+1)
                print("Epoch", epoch+1, "of", numEpochs, ":: Batch", it, "of", totalIt, ":: Epoch:", round((it/totalIt)*100, 4), "%\t-:- Training:", round(((it+(totalIt*epoch))/(totalIt*numEpochs))*100, 6), "%\t -> Loss:", thisLoss, " : Avg Loss:", averageLoss, " @", datetime.datetime.now()) 


        print("Epoch complete @", datetime.datetime.now())

        # Calculate average loss over one epoch
        averageLoss = epochLoss / totalIt
        print(f'Epoch {epoch+1}, Loss: {averageLoss:.4f}')

        if (epoch+1) % snapshots == 0:
            saveModel(model, tokenizer, optimizer, outModel)


    print("=====================================================")
    print("Done with epochs, did ", numEpochs, " epochs. Completed @", datetime.datetime.now())
    print("=====================================================")

    saveModel(model, tokenizer, optimizer, outModel)


def train(inFile: str, pathSave: str, inModel: str = "", numEpochs: int = 3, batchSize: int = 4, prepare: bool = True, snapshots: int = 1):


    print("snapshots: ", snapshots)
    device = getCudaDevice()
    if device == None:
        return

    # Load the model
    model, tokenizer = loadModel(device, inModel)


    if prepare:
        # Load and chunk the data to fit into memory
        chunks = loadData(inFile, 4096)

        dataloader = tokenize(tokenizer, chunks, outModel, batchSize=batchSize)

        numEpochs = 0

    elif not prepare and os.path.isdir(inModel):
        dataloader = loadTokenizedData(inModel, batchSize)
    else:
        print("Either prepare or provide input model")
        return

    # Train for epochs
    doEpochs(device, model, tokenizer, dataloader, numEpochs, snapshots, outModel)

    # Save
    if prepare:
        saveModel(model, tokenizer, outModel=outModel)
        


    # Move output to CPU for decoding
    #print("moving output to cpu...")
    #outputs = outputs.cpu()

    #print("generating outputs...")
    #for i in outputs:
    #        print(tokenizer.decode(i, skip_special_tokens=True))

def parseArgs():
    """
        CLI Args for training and validation
    """
    parser = argparse.ArgumentParser(prog="PicoGPT.py", description="Train, finetune, and generate text with GPT models, even on older hardware like 10XX and earlier.", formatter_class=argparse.RawTextHelpFormatter)

    parser.add_argument('--prepare', '-p', dest='prepare', action='store_const', 
                        const=True, default=False, 
                        help='''It is necessary to prepare the dataset for first time training, chunks and tokenizes.
On resuming training for additional epochs, prepare is not necessary.
''')

    parser.add_argument('--input', '-i', metavar='input.txt', dest='input', action='store',
                        default="",
                        help='Input text, necessary to provide for initial --prepare Optional on resume')

    parser.add_argument('--model', '-m', metavar='model', dest='inModel', action='store',
                        default="gpt2",
                        help='Input model, provide a path to a trained model to resume training. (Default: gpt2)')

    parser.add_argument('--epochs', '-e', metavar='epochs', dest='epochs', action='store',
                        default=3,
                        help='How many epochs to train for (Default: 3)')

    parser.add_argument('--snapshot', '-s', metavar='snapshot', dest='snaps', action='store',
                        default=1,
                        help='How many epochs to run between snapshots saved to disk. (Default: 1)')

    parser.add_argument('--batch-size', '-b', metavar='size', dest='batchSize', action='store',
                        default=4,
                        help='Batch size, adjust lower for GPUs with less memory (Default: 4)')

    parser.add_argument('outModel', help='''Necessary path to save trained model and tokenizer (Example: out/example.model)
This output path is also the same path you can use with --input/-i to resume training.
''')



    args = parser.parse_args()

    if args.prepare and not args.input and args.input != "":
        raise Exception("--prepare/-p must also have an --input/-i")

    return args

if __name__ == "__main__":

    args = parseArgs()

    isPrepare = args.prepare
    inModel   = args.inModel
    inFile    = args.input
    outModel  = args.outModel
    numEpochs = int(args.epochs)
    batchSize = int(args.batchSize)
    snapshots = int(args.snaps)

    # If prepare, don't do epochs
    if isPrepare:
        numEpochs = 0

    try:
        print("PicoGPT v" + str(VERSION))
        print("")

        print("Config for training:")
        print("====================")
        print("Importing model:", inModel)

        if isPrepare:
            print("Prepare selected, will tokenize data")
        else:
            print("Loading tokenized data from:", inModel)

        if args.input:
            print("Training with input data:", inFile)

        print("Saving output model to:", outModel)

        if not isPrepare:
            print("Training for", numEpochs,"epochs with a batch size of", batchSize)
            print("Taking snapshots every", snapshots, "epochs.")

        print("====================")

        print("")
        print("Starting soon. CTRL+C now if you changed your mind.")
        print("Starting in...", end='')
        for i in range(START_DELAY):
            print((START_DELAY-i), ".. ", end='', flush=True)
            time.sleep(1)

        print("Let's gooo....\n")
        #print(inFile, outModel, inModel, numEpochs, batchSize)
        train(inFile, outModel, inModel, numEpochs, batchSize, isPrepare, snapshots) 

    except FileNotFoundError:
        print("\nERROR: Input file not found:", inFile)
    finally:
        print("Exiting...")

