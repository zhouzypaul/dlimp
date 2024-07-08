import glob
import os

import cv2
import numpy as np
import tensorflow_datasets as tfds
from absl import logging

from dataset_builder import MultiThreadedDatasetBuilder


# we ignore the small amount of data that contains >4 views
IMAGE_SIZE = (256, 256)
DEPTH = 6
TRAIN_PROPORTION = 1.0


# Function to read frames from a video and store them as a numpy array
def video_to_frames(video_path):
    # Open the video file
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError("Error opening video file")

    frames = []
    while True:
        # Read next frame
        success, frame = cap.read()
        if not success:
            break

        # Convert BGR to RGB as OpenCV uses BGR by default
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Append the RGB frame to the frames list
        frames.append(frame_rgb)

    # Close the video file
    cap.release()

    # Convert the list of frames to a numpy array
    frames_array = np.stack(frames, axis=0)

    return frames_array


def process_images(path):  # processes images at a trajectory level
    video_dir = os.path.join(path, "trajectory.mp4")
    assert os.path.exists(video_dir), f"Video file {video_dir} does not exist"
    frames = video_to_frames(video_dir)
    return frames


def process_state(path):
    eef = os.path.join(path, "eef_poses.npy")
    return np.load(eef)


def process_actions(path):
    actions_path = os.path.join(path, "actions.npy")
    return list(np.load(actions_path))


def process_lang(path):
    fp = os.path.join(path, "language_text.txt")
    text = ""  # empty string is a placeholder for missing text
    if os.path.exists(fp):
        with open(fp, "r") as f:
            text = f.readline().strip()

    return text


class SOARDataset(MultiThreadedDatasetBuilder):
    """DatasetBuilder for soar dataset."""

    VERSION = tfds.core.Version("1.0.0")
    RELEASE_NOTES = {
        "1.0.0": "Initial release.",
    }
    MANUAL_DOWNLOAD_INSTRUCTIONS = "Please see official release"

    NUM_WORKERS = 16
    CHUNKSIZE = 1000

    def _info(self) -> tfds.core.DatasetInfo:
        """Dataset metadata (homepage, citation,...)."""
        return self.dataset_info_from_configs(
            features=tfds.features.FeaturesDict(
                {
                    "steps": tfds.features.Dataset(
                        {
                            "observation": tfds.features.FeaturesDict(
                                {
                                    "image_0": tfds.features.Image(
                                        shape=IMAGE_SIZE + (3,),
                                        dtype=np.uint8,
                                        encoding_format="jpeg",
                                        doc="Main camera RGB observation (fixed position).",
                                    ),
                                    "state": tfds.features.Tensor(
                                        shape=(7,),
                                        dtype=np.float32,
                                        doc="Robot end effector state, consists of [3x XYZ, 3x roll-pitch-yaw, 1x gripper]",
                                    ),
                                }
                            ),
                            "action": tfds.features.Tensor(
                                shape=(7,),
                                dtype=np.float32,
                                doc="Robot action, consists of [3x XYZ delta, 3x roll-pitch-yaw delta, 1x gripper absolute].",
                            ),
                            "is_first": tfds.features.Scalar(
                                dtype=np.bool_, doc="True on first step of the episode."
                            ),
                            "is_last": tfds.features.Scalar(
                                dtype=np.bool_, doc="True on last step of the episode."
                            ),
                            "language_instruction": tfds.features.Text(
                                doc="Language Instruction."
                            ),
                        }
                    ),
                    "episode_metadata": tfds.features.FeaturesDict(
                        {
                            "file_path": tfds.features.Text(
                                doc="Path to the original data file."
                            ),
                            "has_language": tfds.features.Scalar(
                                dtype=np.bool_,
                                doc="True if language exists in observation, otherwise empty string.",
                            ),
                        }
                    ),
                }
            )
        )

    @classmethod
    def _process_example(cls, example_input):
        """Process a single example."""
        path = example_input

        out = dict()

        out["images"] = process_images(path)
        out["state"] = process_state(path)
        out["actions"] = process_actions(path)
        out["lang"] = process_lang(path)

        assert len(out["actions"]) == len(out["state"]) == len(out["images"]), (
            path,
            len(out["actions"]),
            len(out["state"]),
            len(out["images"]),
        )

        # assemble episode
        episode = []
        episode_metadata = dict()


        instruction = out["lang"]

        for i in range(len(out["actions"])):
            observation = {
                "state": out["state"][i].astype(np.float32),
                "image_0": out["images"][i],
            }

            episode.append(
                {
                    "observation": observation,
                    "action": out["actions"][i].astype(np.float32),
                    "is_first": i == 0,
                    "is_last": i == (len(out["actions"]) - 1),
                    "language_instruction": instruction,
                }
            )

        episode_metadata["file_path"] = path
        episode_metadata["has_language"] = bool(instruction)

        # create output data sample
        sample = {"steps": episode, "episode_metadata": episode_metadata}

        # use episode path as key
        return path, sample

    def _split_generators(self, dl_manager: tfds.download.DownloadManager):
        # each path is a directory that contains dated directories
        paths = glob.glob(os.path.join(dl_manager.manual_dir, *("*" * (DEPTH - 1))))

        train_inputs, val_inputs = [], []

        for path in paths:
            search_path = os.path.join(
                path, "traj*"
            )
            all_traj = glob.glob(search_path)
            if not all_traj:
                print(f"no trajs found in {search_path}")
                continue

            all_inputs = all_traj

            train_inputs += all_inputs[: int(len(all_inputs) * TRAIN_PROPORTION)]
            val_inputs += all_inputs[int(len(all_inputs) * TRAIN_PROPORTION) :]

        logging.info(
            "Converting %d training and %d validation files.",
            len(train_inputs),
            len(val_inputs),
        )
        return {
            "train": iter(train_inputs),
            # "val": iter(val_inputs),
        }
