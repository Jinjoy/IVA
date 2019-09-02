import sys
# import the necessary packages
from keras.applications import ResNet50
from keras.preprocessing.image import img_to_array
from keras.applications import imagenet_utils
from threading import Thread
from PIL import Image
from infer_iva import iva_infer, JsonCustomEncoder
import numpy as np
import base64
import flask
import redis
import uuid
import time
import json
import sys
import io
import cv2

# initialize constants used to control image spatial dimensions and
# data type
IMAGE_WIDTH = 300
IMAGE_HEIGHT = 300
IMAGE_CHANS = 3
IMAGE_DTYPE = "float32"
 
# initialize constants used for server queuing
IMAGE_QUEUE = "image_queue"
BATCH_SIZE = 1
SERVER_SLEEP = 0.25
CLIENT_SLEEP = 0.25


# initialize our Flask application, Redis server, and Keras model
app = flask.Flask(__name__)
db = redis.StrictRedis(host="redis", port=6379, db=0)
model = None


def base64_encode_image(a):
    # base64 encode the input NumPy array
    return base64.b64encode(a).decode("utf-8")
 
def base64_decode_image(a, dtype, shape):
    # if this is Python 3, we need the extra step of encoding the
    # serialized NumPy string as a byte object
    if sys.version_info.major == 3:
        a = bytes(a, encoding="utf-8")
 
    # convert the string to a NumPy array using the supplied data
    # type and target shape
    a = np.frombuffer(base64.decodestring(a), dtype=dtype)
    a = a.reshape(shape)
 
    # return the decoded image
    return a



def prepare_image(image, target):
    # if the image mode is not RGB, convert it
    if image.mode == "RGB":
         image = image.convert("RGB")
 
    # resize the input image and preprocess it
    image = image.resize(target)
    image = img_to_array(image)
    image=cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    
    # return the processed image
    return image

def classify_process():
    # load the pre-trained Keras model (here we are using a model
    # pre-trained on ImageNet and provided by Keras, but you can
    # substitute in your own networks just as easily)
    print("* Loading model...")
    model=iva_infer();
    print("* Model loaded")


    # continually poll for new images to classify
    while True:
        # attempt to grab a batch of images from the database, then
        # initialize the image IDs and batch of images themselves
        queue = db.lrange(IMAGE_QUEUE, 0, BATCH_SIZE - 1)
        imageIDs = []
        batch = None
 
        # loop over the queue
        for q in queue:
            # deserialize the object and obtain the input image
            q = json.loads(q.decode("utf-8"))
            image = base64_decode_image(q["image"], IMAGE_DTYPE,
                (IMAGE_HEIGHT, IMAGE_WIDTH, IMAGE_CHANS))
            # check to see if the batch list is None
            batch = image
            
            # update the image ID
            imageID=q["id"]

            im=image.astype(np.uint8)
            preds = model.infer(im)
            output=json.dumps(preds,cls=JsonCustomEncoder)
 
            # remove the set of images from our queue
            db.ltrim(IMAGE_QUEUE, 1, -1)

            db.set(imageID,output)
 
        # sleep for a small amount
        time.sleep(SERVER_SLEEP)

@app.route("/predict", methods=["POST"])
def predict():
    # initialize the data dictionary that will be returned from the
    # view
    data = {"success": False}
 
    # ensure an image was properly uploaded to our endpoint
    if flask.request.method == "POST":
        if flask.request.files.get("image"):
            # read the image in PIL format and prepare it for
            # classification
            image = flask.request.files["image"].read()
            image = Image.open(io.BytesIO(image))
            image = prepare_image(image, (IMAGE_WIDTH, IMAGE_HEIGHT))
 
            # ensure our NumPy array is C-contiguous as well,
            # otherwise we won't be able to serialize it
            image = image.copy(order="C")
 
            # generate an ID for the classification then add the
            # classification ID + image to the queue
            k = str(uuid.uuid4())
            d = {"id": k, "image": base64_encode_image(image)}
            db.rpush(IMAGE_QUEUE, json.dumps(d))

            # keep looping until our model server returns the output
            # predictions
            while True:
                # attempt to grab the output predictions
                output = db.get(k)
 
                # check to see if our model has classified the input
                # image
                if output is not None:
                    # add the output predictions to our data
                    # dictionary so we can return it to the client
                    output = output.decode("utf-8")
                    data = json.loads(output)
 
                    # delete the result from the database and break
                    # from the polling loop
                    db.delete(k)
                    break
 
                # sleep for a small amount to give the model a chance
                # to classify the input image
                time.sleep(CLIENT_SLEEP)
 
            # indicate that the request was a success
            data["success"] = True
 
    # return the data dictionary as a JSON response
    return flask.jsonify(data)

# if this is the main thread of execution first load the model and
# then start the server
if __name__ == "__main__":
    # load the function used to classify input images in a *separate*
    # thread than the one used for main classification
    print("* Starting model service...")
    t = Thread(target=classify_process, args=())
    t.daemon = True
    t.start()
 
    # start the web server
    print("* Starting web service...")
    app.run(host="0.0.0.0",debug=True) #,use_reloader=True)
