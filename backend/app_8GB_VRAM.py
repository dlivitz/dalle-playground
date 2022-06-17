DALLE_MODEL =  "dalle-mini/dalle-mini/mega-1-fp16:latest"  # can be wandb artifact or 🤗 Hub or local folder or google bucket
DALLE_COMMIT_ID = None

# if the notebook crashes too often you can use dalle-mini instead by uncommenting below line
# DALLE_MODEL = "dalle-mini/dalle-mini/mini-1:v0"

# VQGAN model
VQGAN_REPO = "dalle-mini/vqgan_imagenet_f16_16384"
VQGAN_COMMIT_ID = "e93a26e7707683d349bf5d5c41c5b0ef69b677a9"

from io import BytesIO
from flask import Flask, request, jsonify
from flask_cors import CORS, cross_origin
import base64 

app = Flask(__name__)
CORS(app)
print('--> Starting DALL-E Server. This might take up to two minutes.')

import sys
import jax
import jax.numpy as jnp

# check how many devices are available
jax.local_device_count()

# Load models & tokenizer
from dalle_mini import DalleBart, DalleBartProcessor
from vqgan_jax.modeling_flax_vqgan import VQModel
from transformers import CLIPProcessor, FlaxCLIPModel


from flax.jax_utils import replicate


from functools import partial

model, params_cpu = DalleBart.from_pretrained(
        DALLE_MODEL, revision=DALLE_COMMIT_ID, dtype=jnp.float16, _do_init=False)

processor = DalleBartProcessor.from_pretrained(DALLE_MODEL, revision=DALLE_COMMIT_ID,dtype=jnp.float16)

vqgan, vqgan_params_cpu = VQModel.from_pretrained(VQGAN_REPO, revision=VQGAN_COMMIT_ID, _do_init=False)



# model inference
@partial(jax.pmap, axis_name="batch", static_broadcasted_argnums=(3, 4, 5, 6))
def p_generate(
    tokenized_prompt, key, params, top_k, top_p, temperature, condition_scale):
    return model.generate(
        **tokenized_prompt,
        prng_key=key,
        params=params,
        top_k=top_k,
        top_p=top_p,
        temperature=temperature,
        condition_scale=condition_scale,
    )


# decode image
@partial(jax.pmap, axis_name="batch")
def p_decode(indices, params):
    return vqgan.decode_code(indices, params=params)

import random

# create a random key
seed = random.randint(0, 2**32 - 1)
key = jax.random.PRNGKey(seed)

from dalle_mini import DalleBartProcessor


prompts = ["sunset over a lake in the mountains", "the Eiffel tower landing on the moon"]
tokenized_prompts = processor(prompts)
tokenized_prompt = replicate(tokenized_prompts)
# number of predictions per prompt
n_predictions = 1 #make warmup quicker

# We can customize generation parameters (see https://huggingface.co/blog/how-to-generate)
gen_top_k = None
gen_top_p = None
temperature = None
cond_scale = 10.0
from flax.training.common_utils import shard_prng_key
import numpy as np
from PIL import Image
from tqdm.notebook import trange


def tokenize_prompt(prompt: str):
  tokenized_prompt = processor([prompt])
  return replicate(tokenized_prompt)

def generate_images(prompt:str, num_predictions: int):

  tokenized_prompt = tokenize_prompt(prompt)
  params = replicate(params_cpu)
  
  # create a random key
  seed = random.randint(0, 2**32 - 1)
  key = jax.random.PRNGKey(seed)

  # generate images
  images = []
  r_imgs = []
  for i in range(num_predictions // jax.device_count()):
      # get a new key
      key, subkey = jax.random.split(key)

      # generate images
      encoded_images = p_generate(tokenized_prompt, shard_prng_key(subkey),
          params,gen_top_k, gen_top_p, temperature, cond_scale)

      # remove BOS
      encoded_images = encoded_images.sequences[..., 1:]
      r_imgs.append(np.array(encoded_images))

  del params 

  vqgan_params = replicate(vqgan_params_cpu) 
  
  for encoded_images in r_imgs:
      # decode images
      decoded_images = p_decode(encoded_images, vqgan_params)
      decoded_images = decoded_images.clip(0.0, 1.0).reshape((-1, 256, 256, 3))
      for img in decoded_images:
           images.append(Image.fromarray(np.asarray(img * 255, dtype=np.uint8)))

  del vqgan_params 

  return images


@app.route('/dalle', methods=['POST'])
@cross_origin()
def generate_images_api():
    json_data = request.get_json(force=True)
    text_prompt = json_data["text"]
    num_images = json_data["num_images"]
    generated_imgs = generate_images(text_prompt, num_images)

    generated_images = []
    for img in generated_imgs:
        buffered = BytesIO()
        img.save(buffered, format="JPEG")
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        generated_images.append(img_str)

    print(f'Created {num_images} images from text prompt [{text_prompt}]')
    return jsonify(generated_images)


@app.route('/', methods=['GET'])
@cross_origin()
def health_check():
    return jsonify(success=True)

with app.app_context():
    generate_images("warm-up", 1)
    print('--> DALL-E Server is up and running!')


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(sys.argv[1]), debug=False)
