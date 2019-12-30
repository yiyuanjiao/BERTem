#coding=utf-8
# Copyright 2018 The Google AI Language Team Authors and The HuggingFace Inc. team.
# Copyright (c) 2018, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""BERT finetuning runner."""

from __future__ import absolute_import, division, print_function

import argparse
import csv
import logging
import os
import random

import sys
sys.path.append('..')

import copy
import time
import numpy as np
import torch
from torch.utils.data import (DataLoader, RandomSampler, SequentialSampler,
                              TensorDataset)
from torch.utils.data.distributed import DistributedSampler
from tqdm import tqdm, trange

from torch.nn import CrossEntropyLoss, MSELoss
from scipy.stats import pearsonr, spearmanr
from sklearn.metrics import matthews_corrcoef, f1_score, classification_report,accuracy_score


from pytorch_pretrained_bert.file_utils import PYTORCH_PRETRAINED_BERT_CACHE, WEIGHTS_NAME, CONFIG_NAME
from pytorch_pretrained_bert.modeling import BertForSequenceClassification, BertConfig, \
    BertForSequenceClassificationWithGCN
from pytorch_pretrained_bert.tokenization import BertTokenizer
from pytorch_pretrained_bert.optimization import BertAdam, WarmupLinearSchedule

logger = logging.getLogger(__name__)


class InputExample(object):
    """A single training/test example for simple sequence classification."""

    def __init__(self, guid, text_a, text_b=None, label=None, entity_pos=None):
        """Constructs a InputExample.

        Args:
            guid: Unique id for the example.
            text_a: string. The untokenized text of the first sequence. For single
            sequence tasks, only this sequence must be specified.
            text_b: (Optional) string. The untokenized text of the second sequence.
            Only must be specified for sequence pair tasks.
            label: (Optional) string. The label of the example. This should be
            specified for train and dev examples, but not for test examples.
        """
        self.guid = guid
        self.text_a = text_a
        self.text_b = text_b
        self.label = label
        self.entity_pos = entity_pos

class InputFeatures(object):
    """A single set of features of data."""

    def __init__(self, input_ids, input_mask, segment_ids, label_id, entity_mask=None, entity_seg_pos=None, entity_span1_pos=None, entity_span2_pos=None):
        self.input_ids = input_ids
        self.input_mask = input_mask
        self.segment_ids = segment_ids
        self.label_id = label_id
        self.entity_mask = entity_mask
        self.entity_seg_pos = entity_seg_pos
        self.entity_span1_pos = entity_span1_pos
        self.entity_span2_pos = entity_span2_pos


class DataProcessor(object):
    """Base class for data converters for sequence classification data sets."""

    def get_train_examples(self, data_dir):
        """Gets a collection of `InputExample`s for the train set."""
        raise NotImplementedError()

    def get_dev_examples(self, data_dir):
        """Gets a collection of `InputExample`s for the dev set."""
        raise NotImplementedError()

    def get_labels(self):
        """Gets the list of labels for this data set."""
        raise NotImplementedError()

    @classmethod
    def _read_tsv(cls, input_file, quotechar=None):
        """Reads a tab separated value file."""
        with open(input_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="\t", quotechar=quotechar)
            lines = []
            for line in reader:
                if sys.version_info[0] == 2:
                    line = list(unicode(cell, 'utf-8') for cell in line)
                lines.append(line)
            return lines


class MrpcProcessor(DataProcessor):
    """Processor for the MRPC data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        logger.info("LOOKING AT {}".format(os.path.join(data_dir, "train.tsv")))
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev.tsv")), "dev")

    def get_labels(self):
        """See base class."""
        return ["0", "1"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, i)
            text_a = line[3]
            text_b = line[4]
            label = line[0]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
        return examples

class SemProcessor(DataProcessor):
    """Processor for the SemEval 2010 Task 8 dataset."""

    def get_train_examples(self, data_dir):
        """See base class."""
        logger.info("LOOKING AT {}".format(os.path.join(data_dir, "train.jsonl")))
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.jsonl")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "test.jsonl")), "dev")

    def get_labels(self):
        """See base class."""
        return ['Message-Topic(e2,e1)', 'Instrument-Agency(e2,e1)', 'Entity-Origin(e2,e1)', 'Member-Collection(e1,e2)', 'Member-Collection(e2,e1)', 'Other', 'Component-Whole(e1,e2)', 'Product-Producer(e2,e1)', 'Component-Whole(e2,e1)', 'Entity-Destination(e2,e1)', 'Content-Container(e2,e1)', 'Entity-Destination(e1,e2)', 'Instrument-Agency(e1,e2)', 'Cause-Effect(e2,e1)', 'Entity-Origin(e1,e2)', 'Product-Producer(e1,e2)', 'Cause-Effect(e1,e2)', 'Message-Topic(e1,e2)', 'Content-Container(e1,e2)']

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        import json
        examples = []
        for (i, line) in enumerate(lines):
            guid = "%s-%s" % (set_type, i)
            line = json.loads(line[0])
            text_a = ' '.join(line['tokens'])
            label = line['label']
            entity_pos = line['entities']
            examples.append(
                InputExample(guid=guid, text_a=text_a, label=label, entity_pos = entity_pos))
        return examples


class MnliProcessor(DataProcessor):
    """Processor for the MultiNLI data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev_matched.tsv")),
            "dev_matched")

    def get_labels(self):
        """See base class."""
        return ["contradiction", "entailment", "neutral"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, line[0])
            text_a = line[8]
            text_b = line[9]
            label = line[-1]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
        return examples


class MnliMismatchedProcessor(MnliProcessor):
    """Processor for the MultiNLI Mismatched data set (GLUE version)."""

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev_mismatched.tsv")),
            "dev_matched")


class ColaProcessor(DataProcessor):
    """Processor for the CoLA data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev.tsv")), "dev")

    def get_labels(self):
        """See base class."""
        return ["0", "1"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            guid = "%s-%s" % (set_type, i)
            text_a = line[3]
            label = line[1]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=None, label=label))
        return examples


class Sst2Processor(DataProcessor):
    """Processor for the SST-2 data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev.tsv")), "dev")

    def get_labels(self):
        """See base class."""
        return ["0", "1"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, i)
            text_a = line[0]
            label = line[1]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=None, label=label))
        return examples


class StsbProcessor(DataProcessor):
    """Processor for the STS-B data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev.tsv")), "dev")

    def get_labels(self):
        """See base class."""
        return [None]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, line[0])
            text_a = line[7]
            text_b = line[8]
            label = line[-1]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
        return examples


class QqpProcessor(DataProcessor):
    """Processor for the QQP data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev.tsv")), "dev")

    def get_labels(self):
        """See base class."""
        return ["0", "1"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, line[0])
            try:
                text_a = line[3]
                text_b = line[4]
                label = line[5]
            except IndexError:
                continue
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
        return examples


class QnliProcessor(DataProcessor):
    """Processor for the QNLI data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev.tsv")), 
            "dev_matched")

    def get_labels(self):
        """See base class."""
        return ["entailment", "not_entailment"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, line[0])
            text_a = line[1]
            text_b = line[2]
            label = line[-1]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
        return examples


class RteProcessor(DataProcessor):
    """Processor for the RTE data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev.tsv")), "dev")

    def get_labels(self):
        """See base class."""
        return ["entailment", "not_entailment"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, line[0])
            text_a = line[1]
            text_b = line[2]
            label = line[-1]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
        return examples


class WnliProcessor(DataProcessor):
    """Processor for the WNLI data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev.tsv")), "dev")

    def get_labels(self):
        """See base class."""
        return ["0", "1"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, line[0])
            text_a = line[1]
            text_b = line[2]
            label = line[-1]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
        return examples

def convert_id_list_to_str(id_list):
    s = ''
    for id in id_list:
        s+=str(id)
        s+='_'
    return s

def convert_examples_to_features(examples, label_list, max_seq_length,
                                 tokenizer, output_mode):
    """Loads a data file into a list of `InputBatch`s."""

    label_map = {label : i for i, label in enumerate(label_list)}
    features = []
    entity_set = set()
    entity_pair_set = set()
    for (ex_index, example) in enumerate(examples):
        if ex_index % 10000 == 0:
            logger.info("Writing example %d of %d" % (ex_index, len(examples)))
        old_entity_pos = copy.deepcopy(example.entity_pos)
        tokens_a, new_entity_pos = tokenizer.tokenize(example.text_a,example.entity_pos)
        
        old_entity0 = ''.join(example.text_a.split()[old_entity_pos[0][0]:old_entity_pos[0][1]])
        old_entity1 = ''.join(example.text_a.split()[old_entity_pos[1][0]:old_entity_pos[1][1]])
        new_entity0 = ''.join(tokens_a[new_entity_pos[0][0]:new_entity_pos[0][1]])
        new_entity1 = ''.join(tokens_a[new_entity_pos[1][0]:new_entity_pos[1][1]])
        
        old_entity0 = old_entity0.lower()
        old_entity1 = old_entity1.lower()

        if '##' in new_entity0 or '##' in new_entity1:
            new_entity0 = new_entity0.replace('#','')
            new_entity1 = new_entity1.replace('#','')
        
        try:
            assert(old_entity0 == new_entity0)
            assert(old_entity1 == new_entity1)
        except:
            import pdb;pdb.set_trace()
        if new_entity0=='nuturingrole' or new_entity1=='nuturingrole':
            print(example.text_a.split())
            print('````````````````````````````')
            print(tokens_a)
            time.sleep(100)
        #entity_set.add(new_entity0)
        #entity_set.add(new_entity1)
        #entity_pair_set.add((new_entity0, new_entity1))
        # Entity marker
        tokens_a_ = copy.deepcopy(tokens_a) 
        new_entity_pos_ = copy.deepcopy(new_entity_pos)
        entity1_start, entity1_end = new_entity_pos[0][0], new_entity_pos[0][1] 
        entity2_start, entity2_end = new_entity_pos[1][0], new_entity_pos[1][1] 
        
        tokens_a.insert(entity1_start, '<s1>') 
        new_entity_pos[0][0] = entity1_start
        tokens_a.insert(entity1_end+1, '<e1>')
        new_entity_pos[0][1] = entity1_end+1+1
        tokens_a.insert(entity2_start+2, '<s2>')
        new_entity_pos[1][0] = entity2_start+2
        tokens_a.insert(entity2_end+3,'<e2>')
        new_entity_pos[1][1] = entity2_end+3+1

        if new_entity_pos[1][1] > max_seq_length - 2 - 1:
            import pdb;pdb.set_trace()
            
        tokens_b = None
        if example.text_b:
            tokens_b = tokenizer.tokenize(example.text_b)
            # Modifies `tokens_a` and `tokens_b` in place so that the total
            # length is less than the specified length.
            # Account for [CLS], [SEP], [SEP] with "- 3"
            _truncate_seq_pair(tokens_a, tokens_b, max_seq_length - 3)
        else:
            # Account for [CLS] and [SEP] with "- 2"
            if len(tokens_a) > max_seq_length - 2:
                tokens_a = tokens_a[:(max_seq_length - 2)]

        # The convention in BERT is:
        # (a) For sequence pairs:
        #  tokens:   [CLS] is this jack ##son ##ville ? [SEP] no it is not . [SEP]
        #  type_ids: 0   0  0    0    0     0       0 0    1  1  1  1   1 1
        # (b) For single sequences:
        #  tokens:   [CLS] the dog is hairy . [SEP]
        #  type_ids: 0   0   0   0  0     0 0
        #
        # Where "type_ids" are used to indicate whether this is the first
        # sequence or the second sequence. The embedding vectors for `type=0` and
        # `type=1` were learned during pre-training and are added to the wordpiece
        # embedding vector (and position vector). This is not *strictly* necessary
        # since the [SEP] token unambiguously separates the sequences, but it makes
        # it easier for the model to learn the concept of sequences.
        #
        # For classification tasks, the first vector (corresponding to [CLS]) is
        # used as as the "sentence vector". Note that this only makes sense because
        # the entire model is fine-tuned.
        tokens = ["[CLS]"] + tokens_a + ["[SEP]"]
        segment_ids = [0] * len(tokens)

        if tokens_b:
            tokens += tokens_b + ["[SEP]"]
            segment_ids += [1] * (len(tokens_b) + 1)

        input_ids = tokenizer.convert_tokens_to_ids(tokens)

        # The mask has 1 for real tokens and 0 for padding tokens. Only real
        # tokens are attended to.
        input_mask = [1] * len(input_ids)

        # Zero-pad up to the sequence length.
        padding = [0] * (max_seq_length - len(input_ids))
        input_ids += padding
        input_mask += padding
        segment_ids += padding
        entity_token_ids0 = input_ids[new_entity_pos[0][0]:new_entity_pos[0][1]]
        entity_token_ids1 = input_ids[new_entity_pos[1][0]:new_entity_pos[1][1]]
        e0 = convert_id_list_to_str(entity_token_ids0)
        e1 = convert_id_list_to_str(entity_token_ids1)
        entity_set.add(e0)
        entity_set.add(e1)
        entity_pair_set.add((e0, e1))

        # Used for mention pooling
        entity_mask_tag = 1
        entity_mask = [0] * len(input_ids)
        for entity in new_entity_pos:
            start, end = entity[0],entity[1]
            for i in range(start, end):
                # [CLS], need to +1 offset
                entity_mask[i+1] = entity_mask_tag
        
        """
            Different position embedding
        """
        # Strategy 1
        entity1_pos_tag = 1
        entity2_pos_tag = 2

        entity_seg_pos = [0] * len(input_ids)

        entity1_start, entity1_end = new_entity_pos[0][0], new_entity_pos[0][1] 
        for i in range(entity1_start, entity1_end):
            entity_seg_pos[i+1] = entity1_pos_tag
        entity2_start, entity2_end = new_entity_pos[1][0], new_entity_pos[1][1] 
        for i in range(entity2_start, entity2_end):
            entity_seg_pos[i+1] = entity2_pos_tag
        
        # Strategy 2
        entity_start_pos_tag = 1
        entity_end_pos_tag = 2
        entity_seg_pos_ = [0] * len(input_ids)
        entity1_start, entity1_end = new_entity_pos[0][0], new_entity_pos[0][1] 
        entity_seg_pos_[entity1_start+1] = entity_start_pos_tag
        entity_seg_pos_[entity1_end + 1] = entity_end_pos_tag
        entity2_start, entity2_end = new_entity_pos[1][0], new_entity_pos[1][1] 
        entity_seg_pos_[entity2_start+1] = entity_start_pos_tag
        entity_seg_pos_[entity2_end + 1] = entity_end_pos_tag

        # Strategy 3
        entity_span1_pos = [0] * len(input_ids)
        entity1_start, entity1_end = new_entity_pos[0][0], new_entity_pos[0][1] 
        for i in range(len(entity_span1_pos)):
            if i < entity1_start:
                #entity_span1_pos[i] = np.abs(i - entity1_start)
                entity_span1_pos[i] = i - entity1_start
            elif entity1_start <= i and i < entity1_end:
                entity_span1_pos[i] = 0
            elif i >= entity1_end:
                entity_span1_pos[i] = i - entity1_end + 1
        
        entity_span2_pos = [0] * len(input_ids)
        entity2_start, entity2_end = new_entity_pos[1][0], new_entity_pos[1][1] 
        for i in range(len(entity_span2_pos)):
            if i < entity2_start:
                #entity_span2_pos[i] = np.abs(i - entity2_start)
                entity_span2_pos[i] = i - entity2_start
            elif entity2_start <= i and i < entity2_end:
                entity_span2_pos[i] = 0
            elif i >= entity2_end:
                entity_span2_pos[i] = i - entity2_end + 1

        # Avoid to get negative position to fuck the nn.Embedding
        #entity_span1_pos = [pos+max_seq_length-1 for pos in entity_span1_pos]
        #entity_span2_pos = [pos+max_seq_length-1 for pos in entity_span2_pos]
        
        assert len(input_ids) == max_seq_length
        assert len(input_mask) == max_seq_length
        assert len(segment_ids) == max_seq_length
        assert len(entity_mask) == max_seq_length
        assert len(entity_seg_pos) == max_seq_length
        assert len(entity_seg_pos_) == max_seq_length
        assert len(entity_span1_pos) == max_seq_length
        assert len(entity_span2_pos) == max_seq_length
        if output_mode == "classification":
            label_id = label_map[example.label]
            #lable_id = label_map[''.join(example.lable)]
        elif output_mode == "regression":
            label_id = float(example.label)
        else:
            raise KeyError(output_mode)

        if ex_index < 5:
            logger.info("*** Example ***")
            logger.info("guid: %s" % (example.guid))
            logger.info("tokens: %s" % " ".join(
                    [str(x) for x in tokens]))
            logger.info("input_ids: %s" % " ".join([str(x) for x in input_ids]))
            logger.info("input_mask: %s" % " ".join([str(x) for x in input_mask]))
            logger.info("entity_mask: %s" % " ".join([str(x) for x in entity_mask]))
            logger.info("entity_seg_pos: %s" % " ".join([str(x) for x in entity_seg_pos]))
            logger.info("entity_seg_pos_: %s" % " ".join([str(x) for x in entity_seg_pos_]))
            logger.info("entity_span1_pos: %s" % " ".join([str(x) for x in entity_span1_pos]))
            logger.info("entity_span2_pos: %s" % " ".join([str(x) for x in entity_span2_pos]))
            logger.info(
                    "segment_ids: %s" % " ".join([str(x) for x in segment_ids]))
            logger.info("label: %s (id = %d)" % (example.label, label_id))
        
        #if example.guid == 'train-3':
        #    import pdb;pdb.set_trace()

        features.append(
                InputFeatures(input_ids=input_ids,
                              input_mask=input_mask,
                              segment_ids=segment_ids,
                              label_id=label_id,
                              entity_mask=entity_mask,
                              entity_seg_pos=entity_seg_pos_,
                              entity_span1_pos=entity_span1_pos,
                              entity_span2_pos=entity_span2_pos))
    entity_list = list(entity_set)
    adjacency = torch.zeros(len(entity_list),len(entity_list))
    for entity_pair in enumerate(entity_pair_set):
        adjacency[entity_list.index(entity_pair[1][0])][entity_list.index(entity_pair[1][1])] = 1
        adjacency[entity_list.index(entity_pair[1][1])][entity_list.index(entity_pair[1][0])] = 1
    degree = adjacency.mm(torch.ones(len(entity_list), 1))
    d_ = degree+torch.ones(len(entity_list), 1)
    d = torch.diag(d_.pow(-0.5).view(-1))
    adjacency_ = adjacency+torch.diag(torch.ones(len(entity_list)))
    spectral = d.mm(adjacency_).mm(d)
    return features, entity_list, degree, spectral


def _truncate_seq_pair(tokens_a, tokens_b, max_length):
    """Truncates a sequence pair in place to the maximum length."""

    # This is a simple heuristic which will always truncate the longer sequence
    # one token at a time. This makes more sense than truncating an equal percent
    # of tokens from each, since if one sequence is very short then each token
    # that's truncated likely contains more information than a longer sequence.
    while True:
        total_length = len(tokens_a) + len(tokens_b)
        if total_length <= max_length:
            break
        if len(tokens_a) > len(tokens_b):
            tokens_a.pop()
        else:
            tokens_b.pop()


def simple_accuracy(preds, labels):
    return (preds == labels).mean()


def acc_and_f1(preds, labels):
    acc = accuracy_score(labels, preds)
    f1 = f1_score(y_true=labels, y_pred=preds,average='macro')
    report = classification_report(labels, preds)
    return {
        "acc": acc,
        "f1": f1,
        "acc_and_f1": (acc + f1) / 2,
        "report": report
    }


def pearson_and_spearman(preds, labels):
    pearson_corr = pearsonr(preds, labels)[0]
    spearman_corr = spearmanr(preds, labels)[0]
    return {
        "pearson": pearson_corr,
        "spearmanr": spearman_corr,
        "corr": (pearson_corr + spearman_corr) / 2,
    }


def compute_metrics(task_name, preds, labels):
    assert len(preds) == len(labels)
    if task_name == "cola":
        return {"mcc": matthews_corrcoef(labels, preds)}
    elif task_name == "sst-2":
        return {"acc": simple_accuracy(preds, labels)}
    elif task_name == "mrpc":
        return acc_and_f1(preds, labels)
    elif task_name == "sem":
        return acc_and_f1(preds, labels)
    elif task_name == "sts-b":
        return pearson_and_spearman(preds, labels)
    elif task_name == "qqp":
        return acc_and_f1(preds, labels)
    elif task_name == "mnli":
        return {"acc": simple_accuracy(preds, labels)}
    elif task_name == "mnli-mm":
        return {"acc": simple_accuracy(preds, labels)}
    elif task_name == "qnli":
        return {"acc": simple_accuracy(preds, labels)}
    elif task_name == "rte":
        return {"acc": simple_accuracy(preds, labels)}
    elif task_name == "wnli":
        return {"acc": simple_accuracy(preds, labels)}
    else:
        raise KeyError(task_name)


def main():
    parser = argparse.ArgumentParser()

    ## Required parameters
    parser.add_argument("--data_dir",
                        default=None,
                        type=str,
                        required=True,
                        help="The input data dir. Should contain the .tsv files (or other data files) for the task.")
    parser.add_argument("--bert_model", default=None, type=str, required=True,
                        help="Bert pre-trained model selected in the list: bert-base-uncased, "
                        "bert-large-uncased, bert-base-cased, bert-large-cased, bert-base-multilingual-uncased, "
                        "bert-base-multilingual-cased, bert-base-chinese.")
    parser.add_argument("--task_name",
                        default=None,
                        type=str,
                        required=True,
                        help="The name of the task to train.")
    parser.add_argument("--output_dir",
                        default=None,
                        type=str,
                        required=True,
                        help="The output directory where the model predictions and checkpoints will be written.")

    ## Other parameters
    parser.add_argument("--cache_dir",
                        default="",
                        type=str,
                        help="Where do you want to store the pre-trained models downloaded from s3")
    parser.add_argument("--max_seq_length",
                        default=128,
                        type=int,
                        help="The maximum total input sequence length after WordPiece tokenization. \n"
                             "Sequences longer than this will be truncated, and sequences shorter \n"
                             "than this will be padded.")
    parser.add_argument("--do_train",
                        action='store_true',
                        help="Whether to run training.")
    parser.add_argument("--do_eval",
                        action='store_true',
                        help="Whether to run eval on the dev set.")
    parser.add_argument("--do_lower_case",
                        action='store_true',
                        help="Set this flag if you are using an uncased model.")
    parser.add_argument("--train_batch_size",
                        default=32,
                        type=int,
                        help="Total batch size for training.")
    parser.add_argument("--eval_batch_size",
                        default=8,
                        type=int,
                        help="Total batch size for eval.")
    parser.add_argument("--learning_rate",
                        default=5e-5,
                        type=float,
                        help="The initial learning rate for Adam.")
    parser.add_argument("--num_train_epochs",
                        default=3.0,
                        type=float,
                        help="Total number of training epochs to perform.")
    parser.add_argument("--warmup_proportion",
                        default=0.1,
                        type=float,
                        help="Proportion of training to perform linear learning rate warmup for. "
                             "E.g., 0.1 = 10%% of training.")
    parser.add_argument("--no_cuda",
                        action='store_true',
                        help="Whether not to use CUDA when available")
    parser.add_argument("--local_rank",
                        type=int,
                        default=-1,
                        help="local_rank for distributed training on gpus")
    parser.add_argument('--seed',
                        type=int,
                        default=42,
                        help="random seed for initialization")
    parser.add_argument('--gradient_accumulation_steps',
                        type=int,
                        default=1,
                        help="Number of updates steps to accumulate before performing a backward/update pass.")
    parser.add_argument('--fp16',
                        action='store_true',
                        help="Whether to use 16-bit float precision instead of 32-bit")
    parser.add_argument('--loss_scale',
                        type=float, default=0,
                        help="Loss scaling to improve fp16 numeric stability. Only used when fp16 set to True.\n"
                             "0 (default value): dynamic loss scaling.\n"
                             "Positive power of 2: static loss scaling value.\n")
    parser.add_argument('--server_ip', type=str, default='', help="Can be used for distant debugging.")
    parser.add_argument('--server_port', type=str, default='', help="Can be used for distant debugging.")
    args = parser.parse_args()

    if args.server_ip and args.server_port:
        # Distant debugging - see https://code.visualstudio.com/docs/python/debugging#_attach-to-a-local-script
        import ptvsd
        print("Waiting for debugger attach")
        ptvsd.enable_attach(address=(args.server_ip, args.server_port), redirect_output=True)
        ptvsd.wait_for_attach()

    processors = {
        "cola": ColaProcessor,
        "mnli": MnliProcessor,
        "mnli-mm": MnliMismatchedProcessor,
        "mrpc": MrpcProcessor,
        "sem": SemProcessor,
        "sst-2": Sst2Processor,
        "sts-b": StsbProcessor,
        "qqp": QqpProcessor,
        "qnli": QnliProcessor,
        "rte": RteProcessor,
        "wnli": WnliProcessor,
    }

    output_modes = {
        "cola": "classification",
        "mnli": "classification",
        "mrpc": "classification",
        "sem": "classification",
        "sst-2": "classification",
        "sts-b": "regression",
        "qqp": "classification",
        "qnli": "classification",
        "rte": "classification",
        "wnli": "classification",
    }

    if args.local_rank == -1 or args.no_cuda:
        device = torch.device("cuda" if torch.cuda.is_available() and not args.no_cuda else "cpu")
        n_gpu = torch.cuda.device_count()
    else:
        torch.cuda.set_device(args.local_rank)
        device = torch.device("cuda", args.local_rank)
        n_gpu = 1
        # Initializes the distributed backend which will take care of sychronizing nodes/GPUs
        torch.distributed.init_process_group(backend='nccl')

    logging.basicConfig(format = '%(asctime)s - %(levelname)s - %(name)s -   %(message)s',
                        datefmt = '%m/%d/%Y %H:%M:%S',
                        level = logging.INFO if args.local_rank in [-1, 0] else logging.WARN)

    logger.info("device: {} n_gpu: {}, distributed training: {}, 16-bits training: {}".format(
        device, n_gpu, bool(args.local_rank != -1), args.fp16))

    if args.gradient_accumulation_steps < 1:
        raise ValueError("Invalid gradient_accumulation_steps parameter: {}, should be >= 1".format(
                            args.gradient_accumulation_steps))

    args.train_batch_size = args.train_batch_size // args.gradient_accumulation_steps

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if n_gpu > 0:
        torch.cuda.manual_seed_all(args.seed)

    if not args.do_train and not args.do_eval:
        raise ValueError("At least one of `do_train` or `do_eval` must be True.")

    if os.path.exists(args.output_dir) and os.listdir(args.output_dir) and args.do_train:
        raise ValueError("Output directory ({}) already exists and is not empty.".format(args.output_dir))
    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir)

    task_name = args.task_name.lower()

    if task_name not in processors:
        raise ValueError("Task not found: %s" % (task_name))

    processor = processors[task_name]()
    output_mode = output_modes[task_name]

    label_list = processor.get_labels()
    num_labels = len(label_list)
    tokenizer = BertTokenizer.from_pretrained(args.bert_model, do_lower_case=args.do_lower_case)
    train_examples = None
    num_train_optimization_steps = None
    if args.do_train:
        train_examples = processor.get_train_examples(args.data_dir)
        num_train_optimization_steps = int(
            len(train_examples) / args.train_batch_size / args.gradient_accumulation_steps) * args.num_train_epochs
        if args.local_rank != -1:
            num_train_optimization_steps = num_train_optimization_steps // torch.distributed.get_world_size()

    # Prepare model
    cache_dir = args.cache_dir if args.cache_dir else os.path.join(str(PYTORCH_PRETRAINED_BERT_CACHE), 'distributed_{}'.format(args.local_rank))
    model = BertForSequenceClassificationWithGCN.from_pretrained(args.bert_model,
              cache_dir=cache_dir,
              num_labels=num_labels)
    if args.fp16:
        model.half()
    model.to(device)
    if args.local_rank != -1:
        try:
            from apex.parallel import DistributedDataParallel as DDP
        except ImportError:
            raise ImportError("Please install apex from https://www.github.com/nvidia/apex to use distributed and fp16 training.")

        model = DDP(model)
    elif n_gpu > 1:
        model = torch.nn.DataParallel(model)

    # Prepare optimizer
    if args.do_train:
        param_optimizer = list(model.named_parameters())
        #no_decay = ['bias', 'LayerNorm.bias', 'LayerNorm.weight']
        no_decay = []
        optimizer_grouped_parameters = [
            {'params': [p for n, p in param_optimizer if not any(nd in n for nd in no_decay)], 'weight_decay': 0.01},
            {'params': [p for n, p in param_optimizer if any(nd in n for nd in no_decay)], 'weight_decay': 0.0}
            ]
        if args.fp16:
            try:
                from apex.optimizers import FP16_Optimizer
                from apex.optimizers import FusedAdam
            except ImportError:
                raise ImportError("Please install apex from https://www.github.com/nvidia/apex to use distributed and fp16 training.")

            optimizer = FusedAdam(optimizer_grouped_parameters,
                                  lr=args.learning_rate,
                                  bias_correction=False,
                                  max_grad_norm=1.0)
            if args.loss_scale == 0:
                optimizer = FP16_Optimizer(optimizer, dynamic_loss_scale=True)
            else:
                optimizer = FP16_Optimizer(optimizer, static_loss_scale=args.loss_scale)
            warmup_linear = WarmupLinearSchedule(warmup=args.warmup_proportion,
                                                 t_total=num_train_optimization_steps)

        else:
            optimizer = BertAdam(optimizer_grouped_parameters,
                                 lr=args.learning_rate,
                                 warmup=args.warmup_proportion,
                                 t_total=num_train_optimization_steps)

    global_step = 0
    nb_tr_steps = 0
    tr_loss = 0
    if args.do_train:
        train_features, train_entity_list, train_degree, train_spectral = convert_examples_to_features(
            train_examples, label_list, args.max_seq_length, tokenizer, output_mode)
        logger.info("***** Running training *****")
        logger.info("  Num examples = %d", len(train_examples))
        logger.info("  Batch size = %d", args.train_batch_size)
        logger.info("  Num steps = %d", num_train_optimization_steps)
        all_input_ids = torch.tensor([f.input_ids for f in train_features], dtype=torch.long)
        all_input_mask = torch.tensor([f.input_mask for f in train_features], dtype=torch.long)
        # FloatTensor(forward)
        all_entity_mask = torch.tensor([f.entity_mask for f in train_features], dtype=torch.float)
        all_entity_seg_pos = torch.tensor([f.entity_seg_pos for f in train_features], dtype=torch.long)
        all_entity_span1_pos = torch.tensor([f.entity_span1_pos for f in train_features], dtype=torch.float)
        all_entity_span2_pos = torch.tensor([f.entity_span2_pos for f in train_features], dtype=torch.float)
        all_segment_ids = torch.tensor([f.segment_ids for f in train_features], dtype=torch.long)
        if output_mode == "classification":
            all_label_ids = torch.tensor([f.label_id for f in train_features], dtype=torch.long)
        elif output_mode == "regression":
            all_label_ids = torch.tensor([f.label_id for f in train_features], dtype=torch.float)

        train_data = TensorDataset(all_input_ids, all_input_mask, all_entity_mask, all_entity_seg_pos, all_entity_span1_pos, all_entity_span2_pos, all_segment_ids, all_label_ids)
        if args.local_rank == -1:
            train_sampler = RandomSampler(train_data)
        else:
            train_sampler = DistributedSampler(train_data)
        train_dataloader = DataLoader(train_data, sampler=train_sampler, batch_size=args.train_batch_size)

        

        # do eval
        eval_examples = processor.get_dev_examples(args.data_dir)
        eval_features = convert_examples_to_features(
            eval_examples, label_list, args.max_seq_length, tokenizer, output_mode)
        logger.info("***** evaluation paras*****")
        logger.info("  Num examples = %d", len(eval_examples))
        logger.info("  Batch size = %d", args.eval_batch_size)
        eval_input_ids = torch.tensor([f.input_ids for f in eval_features], dtype=torch.long)
        eval_input_mask = torch.tensor([f.input_mask for f in eval_features], dtype=torch.long)
        eval_entity_mask = torch.tensor([f.entity_mask for f in eval_features], dtype=torch.float)
        eval_entity_seg_pos = torch.tensor([f.entity_seg_pos for f in eval_features], dtype=torch.long)
        eval_entity_span1_pos = torch.tensor([f.entity_span1_pos for f in eval_features], dtype=torch.float)
        eval_entity_span2_pos = torch.tensor([f.entity_span2_pos for f in eval_features], dtype=torch.float)
        eval_segment_ids = torch.tensor([f.segment_ids for f in eval_features], dtype=torch.long)

        if output_mode == "classification":
            eval_label_ids = torch.tensor([f.label_id for f in eval_features], dtype=torch.long)
        elif output_mode == "regression":
            eval_label_ids = torch.tensor([f.label_id for f in eval_features], dtype=torch.float)

        eval_data = TensorDataset(eval_input_ids, eval_input_mask, eval_entity_mask, eval_entity_seg_pos,
                                  eval_entity_span1_pos, eval_entity_span2_pos, eval_segment_ids, eval_label_ids)
        # Run prediction for full data
        eval_sampler = SequentialSampler(eval_data)
        eval_dataloader = DataLoader(eval_data, sampler=eval_sampler, batch_size=args.eval_batch_size)
        


        # epoch_label_ids = []
        for _ in trange(int(args.num_train_epochs), desc="Epoch"):
            model.train()
            epoch_step = 0
            tr_loss = 0
            nb_tr_examples, nb_tr_steps = 0, 0
            epoch_label_ids = []
            tr_preds = []

            train_entity_representation = torch.zeros(len(train_entity_list), model.config.hidden_size)
            for step, batch in enumerate(tqdm(train_dataloader), desc="Iteration"):
                batch = tuple(t.to(device) for t in batch)
                input_ids, input_mask, entity_mask, entity_seg_pos, entity_span1_pos, entity_span2_pos, segment_ids, label_ids = batch
                model.get_representation(input_ids, segment_ids, input_mask, entity_mask, entity_seg_pos, entity_span1_pos, entity_span2_pos, train_entity_list, train_entity_representation, labels=None)
            train_degree_rep = torch.Tensor(len(train_entity_list), model.config.hidden_size)
            train_degree_rep.copy_(train_degree)
            train_entity_representation = train_entity_representation.div(train_degree_rep)
            print("````````````````````````````````````````")
            print(len(train_entity_representation))
            print(len(train_entity_representation[0]))
            print(train_entity_representation[0])
            print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            time.sleep(100)

            for step, batch in enumerate(tqdm(train_dataloader, desc="Iteration")):
                batch = tuple(t.to(device) for t in batch)
                input_ids, input_mask, entity_mask, entity_seg_pos, entity_span1_pos, entity_span2_pos, segment_ids, label_ids = batch
                # define a new function to compute loss values for both output_modes
                logits = model(input_ids, segment_ids, input_mask, entity_mask, entity_seg_pos, entity_span1_pos, entity_span2_pos, train_entity_representation, train_spectral, labels=None)

                if output_mode == "classification":
                    loss_fct = CrossEntropyLoss()
                    loss = loss_fct(logits.view(-1, num_labels), label_ids.view(-1))
                elif output_mode == "regression":
                    loss_fct = MSELoss()
                    loss = loss_fct(logits.view(-1), label_ids.view(-1))

                if n_gpu > 1:
                    loss = loss.mean() # mean() to average on multi-gpu.
                if args.gradient_accumulation_steps > 1:
                    loss = loss / args.gradient_accumulation_steps

                if args.fp16:
                    optimizer.backward(loss)
                else:
                    loss.backward()

                tr_loss += loss.item()
                nb_tr_examples += input_ids.size(0)
                nb_tr_steps += 1
                if (step + 1) % args.gradient_accumulation_steps == 0:
                    if args.fp16:
                        # modify learning rate with special warm up BERT uses
                        # if args.fp16 is False, BertAdam is used that handles this automatically
                        lr_this_step = args.learning_rate * warmup_linear.get_lr(global_step, args.warmup_proportion)
                        for param_group in optimizer.param_groups:
                            param_group['lr'] = lr_this_step
                    optimizer.step()
                    optimizer.zero_grad()
                    global_step += 1
                    epoch_step += 1
                if len(tr_preds) == 0:
                    tr_preds.append(logits.detach().cpu().numpy())
                else:
                    tr_preds[0] = np.append(
                        tr_preds[0], logits.detach().cpu().numpy(), axis=0)
                if len(epoch_label_ids) == 0:
                    epoch_label_ids.append(label_ids.view(-1).cpu().numpy())
                else:
                    epoch_label_ids[0] = np.append(
                        epoch_label_ids[0], label_ids.view(-1).cpu().numpy(), axis=0)
                logger.info(" batch_loss = %s",loss.item())
            tr_preds = tr_preds[0]
            epoch_label_ids = epoch_label_ids[0]
            if output_mode == "classification":
                tr_preds = np.argmax(tr_preds, axis=1)
            elif output_mode == "regression":
                tr_preds = np.squeeze(tr_preds)
            tr_result = compute_metrics(task_name, tr_preds, epoch_label_ids)
            train_loss = tr_loss / epoch_step if args.do_train else None

            tr_result['train_loss'] = train_loss
            for key in sorted(tr_result.keys()):
                logger.info("  %s = %s", key, str(tr_result[key]))

            #eval
            model.eval()
            eval_loss = 0
            nb_eval_steps = 0
            preds = []
            for input_ids, input_mask, entity_mask, entity_seg_pos, entity_span1_pos, entity_span2_pos, segment_ids, label_ids in tqdm(
                    eval_dataloader, desc="Evaluating"):
                input_ids = input_ids.to(device)
                input_mask = input_mask.to(device)
                entity_mask = entity_mask.to(device)
                entity_seg_pos = entity_seg_pos.to(device)
                entity_span1_pos = entity_span1_pos.to(device)
                entity_span2_pos = entity_span2_pos.to(device)
                segment_ids = segment_ids.to(device)
                label_ids = label_ids.to(device)
                with torch.no_grad():
                    logits = model(input_ids, segment_ids, input_mask, entity_mask, entity_seg_pos, entity_span1_pos,
                                   entity_span2_pos, labels=None)
                    # logits = model(input_ids, segment_ids, input_mask, labels=None)

                # create eval loss and other metric required by the task
                if output_mode == "classification":
                    loss_fct = CrossEntropyLoss()
                    tmp_eval_loss = loss_fct(logits.view(-1, num_labels), label_ids.view(-1))
                elif output_mode == "regression":
                    loss_fct = MSELoss()
                    tmp_eval_loss = loss_fct(logits.view(-1), label_ids.view(-1))

                eval_loss += tmp_eval_loss.mean().item()
                nb_eval_steps += 1
                if len(preds) == 0:
                    preds.append(logits.detach().cpu().numpy())
                else:
                    preds[0] = np.append(
                        preds[0], logits.detach().cpu().numpy(), axis=0)
            eval_loss = eval_loss / nb_eval_steps
            preds = preds[0]
            if output_mode == "classification":
                preds = np.argmax(preds, axis=1)
            elif output_mode == "regression":
                preds = np.squeeze(preds)
            result = compute_metrics(task_name, preds, eval_label_ids.numpy())
            loss = tr_loss / global_step if args.do_train else None

            result['eval_loss'] = eval_loss
            result['global_step'] = global_step
            result['loss'] = loss

            output_eval_file = os.path.join(args.output_dir, "eval_results.txt")
            with open(output_eval_file, "a") as writer:
                logger.info("***** Eval results *****")
                for key in sorted(result.keys()):
                    logger.info("  %s = %s", key, str(result[key]))
                    writer.write("%s = %s\n" % (key, str(result[key])))
                for i in range(0,10):
                    writer.write("\n")


    if args.do_train and (args.local_rank == -1 or torch.distributed.get_rank() == 0):
        # Save a trained model, configuration and tokenizer
        model_to_save = model.module if hasattr(model, 'module') else model  # Only save the model it-self

        # If we save using the predefined names, we can load using `from_pretrained`
        output_model_file = os.path.join(args.output_dir, WEIGHTS_NAME)
        output_config_file = os.path.join(args.output_dir, CONFIG_NAME)

        torch.save(model_to_save.state_dict(), output_model_file)
        model_to_save.config.to_json_file(output_config_file)
        tokenizer.save_vocabulary(args.output_dir)

        # Load a trained model and vocabulary that you have fine-tuned
        model = BertForSequenceClassification.from_pretrained(args.output_dir, num_labels=num_labels)
        tokenizer = BertTokenizer.from_pretrained(args.output_dir, do_lower_case=args.do_lower_case)
    else:
        model = BertForSequenceClassification.from_pretrained(args.bert_model, num_labels=num_labels)
    model.to(device)


if __name__ == "__main__":
    main()
