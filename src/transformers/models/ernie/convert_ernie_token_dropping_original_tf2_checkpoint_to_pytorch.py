# Copyright 2022 The HuggingFace Team. All rights reserved.
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

"""
This script converts a lm-head checkpoint from the "Token Dropping" implementation into a PyTorch-compatible ERNIE
model. The official implementation of "Token Dropping" can be found in the TensorFlow Models repository:

https://github.com/tensorflow/models/tree/master/official/projects/token_dropping
"""
import argparse

import tensorflow as tf
import torch

from transformers import ErnieConfig, ErnieForMaskedLM
from transformers.models.ernie.modeling_ernie import (
    ErnieIntermediate,
    ErnieLayer,
    ErnieOutput,
    ErniePooler,
    ErnieSelfAttention,
    ErnieSelfOutput,
)
from transformers.utils import logging


logging.set_verbosity_info()


def convert_checkpoint_to_pytorch(tf_checkpoint_path: str, config_path: str, pytorch_dump_path: str):
    def get_masked_lm_array(name: str):
        full_name = f"masked_lm/{name}/.ATTRIBUTES/VARIABLE_VALUE"
        array = tf.train.load_variable(tf_checkpoint_path, full_name)

        if "kernel" in name:
            array = array.transpose()

        return torch.from_numpy(array)

    def get_encoder_array(name: str):
        full_name = f"encoder/{name}/.ATTRIBUTES/VARIABLE_VALUE"
        array = tf.train.load_variable(tf_checkpoint_path, full_name)

        if "kernel" in name:
            array = array.transpose()

        return torch.from_numpy(array)

    def get_encoder_layer_array(layer_index: int, name: str):
        full_name = f"encoder/_transformer_layers/{layer_index}/{name}/.ATTRIBUTES/VARIABLE_VALUE"
        array = tf.train.load_variable(tf_checkpoint_path, full_name)

        if "kernel" in name:
            array = array.transpose()

        return torch.from_numpy(array)

    def get_encoder_attention_layer_array(layer_index: int, name: str, orginal_shape):
        full_name = f"encoder/_transformer_layers/{layer_index}/_attention_layer/{name}/.ATTRIBUTES/VARIABLE_VALUE"
        array = tf.train.load_variable(tf_checkpoint_path, full_name)
        array = array.reshape(orginal_shape)

        if "kernel" in name:
            array = array.transpose()

        return torch.from_numpy(array)

    print(f"Loading model based on config from {config_path}...")
    config = ErnieConfig.from_json_file(config_path)
    model = ErnieForMaskedLM(config)

    # Layers
    for layer_index in range(0, config.num_hidden_layers):
        layer: ErnieLayer = model.ernie.encoder.layer[layer_index]

        # Self-attention
        self_attn: ErnieSelfAttention = layer.attention.self

        self_attn.query.weight.data = get_encoder_attention_layer_array(
            layer_index, "_query_dense/kernel", self_attn.query.weight.data.shape
        )
        self_attn.query.bias.data = get_encoder_attention_layer_array(
            layer_index, "_query_dense/bias", self_attn.query.bias.data.shape
        )
        self_attn.key.weight.data = get_encoder_attention_layer_array(
            layer_index, "_key_dense/kernel", self_attn.key.weight.data.shape
        )
        self_attn.key.bias.data = get_encoder_attention_layer_array(
            layer_index, "_key_dense/bias", self_attn.key.bias.data.shape
        )
        self_attn.value.weight.data = get_encoder_attention_layer_array(
            layer_index, "_value_dense/kernel", self_attn.value.weight.data.shape
        )
        self_attn.value.bias.data = get_encoder_attention_layer_array(
            layer_index, "_value_dense/bias", self_attn.value.bias.data.shape
        )

        # Self-attention Output
        self_output: ErnieSelfOutput = layer.attention.output

        self_output.dense.weight.data = get_encoder_attention_layer_array(
            layer_index, "_output_dense/kernel", self_output.dense.weight.data.shape
        )
        self_output.dense.bias.data = get_encoder_attention_layer_array(
            layer_index, "_output_dense/bias", self_output.dense.bias.data.shape
        )

        self_output.LayerNorm.weight.data = get_encoder_layer_array(layer_index, "_attention_layer_norm/gamma")
        self_output.LayerNorm.bias.data = get_encoder_layer_array(layer_index, "_attention_layer_norm/beta")

        # Intermediate
        intermediate: ErnieIntermediate = layer.intermediate

        intermediate.dense.weight.data = get_encoder_layer_array(layer_index, "_intermediate_dense/kernel")
        intermediate.dense.bias.data = get_encoder_layer_array(layer_index, "_intermediate_dense/bias")

        # Output
        ernie_output: ErnieOutput = layer.output

        ernie_output.dense.weight.data = get_encoder_layer_array(layer_index, "_output_dense/kernel")
        ernie_output.dense.bias.data = get_encoder_layer_array(layer_index, "_output_dense/bias")

        ernie_output.LayerNorm.weight.data = get_encoder_layer_array(layer_index, "_output_layer_norm/gamma")
        ernie_output.LayerNorm.bias.data = get_encoder_layer_array(layer_index, "_output_layer_norm/beta")

    # Embeddings
    model.ernie.embeddings.position_embeddings.weight.data = get_encoder_array("_position_embedding_layer/embeddings")
    model.ernie.embeddings.token_type_embeddings.weight.data = get_encoder_array("_type_embedding_layer/embeddings")
    model.ernie.embeddings.LayerNorm.weight.data = get_encoder_array("_embedding_norm_layer/gamma")
    model.ernie.embeddings.LayerNorm.bias.data = get_encoder_array("_embedding_norm_layer/beta")

    # LM Head
    lm_head = model.cls.predictions.transform

    lm_head.dense.weight.data = get_masked_lm_array("dense/kernel")
    lm_head.dense.bias.data = get_masked_lm_array("dense/bias")

    lm_head.LayerNorm.weight.data = get_masked_lm_array("layer_norm/gamma")
    lm_head.LayerNorm.bias.data = get_masked_lm_array("layer_norm/beta")

    model.ernie.embeddings.word_embeddings.weight.data = get_masked_lm_array("embedding_table")

    # Pooling
    model.ernie.pooler = ErniePooler(config=config)
    model.ernie.pooler.dense.weight.data: ErniePooler = get_encoder_array("_pooler_layer/kernel")
    model.ernie.pooler.dense.bias.data: ErniePooler = get_encoder_array("_pooler_layer/bias")

    # Export final model
    model.save_pretrained(pytorch_dump_path)

    # Integration test - should load without any errors ;)
    new_model = ErnieForMaskedLM.from_pretrained(pytorch_dump_path)
    print(new_model.eval())

    print("Model conversion was done sucessfully!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tf_checkpoint_path", type=str, required=True, help="Path to the TensorFlow Token Dropping checkpoint path."
    )
    parser.add_argument(
        "--ernie_config_file",
        type=str,
        required=True,
        help="The config json file corresponding to the ERNIE model. This specifies the model architecture.",
    )
    parser.add_argument(
        "--pytorch_dump_path",
        type=str,
        required=True,
        help="Path to the output PyTorch model.",
    )
    args = parser.parse_args()
    convert_checkpoint_to_pytorch(args.tf_checkpoint_path, args.ernie_config_file, args.pytorch_dump_path)
