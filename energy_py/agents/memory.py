"""

"""

import collections
import itertools
import os

import numpy as np
import pandas as pd

from energy_py.main.scripts.utils import ensure_dir
from energy_py.main.scripts.visualizers import Agent_Memory_Visualizer

class Agent_Memory(Agent_Memory_Visualizer):
    """
    inherits from Visualizer!

    A class to hold the memory of an agent

    Contains functions to process the memory for an agent to learn from
    """
    def __init__(self, memory_length,
                       observation_space,
                       action_space,
                       reward_space,
                       discount_rate):

        super().__init__()
        self.memory_length     = memory_length
        self.observation_space = observation_space
        self.action_space      = action_space
        self.reward_space      = reward_space
        self.discount_rate     = discount_rate

        #  a named tuple to hold experience
        self.Experience = collections.namedtuple('experience', 'observation, action, reward, next_observation, step, episode')
        self.Scaled_Experience = collections.namedtuple('scaled_experience', 'observation, action, reward, next_observation, step, episode, discounted_return')

        self.training_data = []  #  TODO

        self.reset()

    def reset(self):
        """
        Resets the memory object
        """
        self.experiences        = []
        self.scaled_experiences = []
        self.discounted_returns = np.array([])
        self.outputs  = collections.defaultdict(list)
        self.losses = []

    def normalize(self, value, low, high):
        """
        Helper function
        Normalizes a value
        """
        #  if statement to catch the constant value case
        if low == high:
            normalized = 0
        else:
            max_range = high - low
            normalized = (value - low) / max_range
        return np.array(normalized)

    def make_dummy_array(self, value, space):
        """
        Helper function
        Creates an array of dummy variables

        Not needed anymore
        """
        #  pull out the discrete_space space array
        discrete_space = space.discrete_space
        #  create an array of zeros
        scaled = np.zeros(discrete_space.shape)
        #  set to 1 where this value occurs
        scaled[np.where(discrete_space == value)] = 1
        #  quick check that we only have one dummy variable
        assert np.sum(scaled) == 1
        return scaled

    def scale_array(self, array, space):
        """
        Helper function for scale_experience()
        Uses the space & a given function to scale an array
        Default scaler is to normalize

        Used to scale the observation and action
        """

        #  empty numpy array
        scaled_array = np.array([])

        #  iterate across the array values & corresponding space object
        for value, spc in itertools.zip_longest(array, space):
            if spc.type == 'continuous':  #  normalize continuous variables
                scaled = self.normalize(value,
                                     spc.low,
                                     spc.high)

            elif spc.type == 'discrete':  #  shouldn't need to do anything
                #  check value is already dummy
                assert (value == 0) or (value == 1)
            else:
                assert 1 == 0

            #  appending the scaled value onto the scaled array
            scaled_array = np.append(scaled_array, scaled)

        scaled_array = scaled_array.reshape(-1)

        return scaled_array

    def scale_reward(self, reward, space):
        """
        Helper function for scale_experience()
        Uses a space to scale the reward
        """
        return self.normalize(reward, space.low, space.high)

    def scale_experience(self, exp, discounted_return=None):
        """
        Helper function for add_experience
        Scales a given experience tuple

        Discounted return is an optimal arg so that the scaled_exp array can
        be created at any time
        """
        scaled_obs = self.scale_array(exp.observation, self.observation_space)
        scaled_reward = self.scale_reward(exp.reward, self.reward_space)

        #  making a named tuple for the scaled experience
        scaled_exp = self.Scaled_Experience(scaled_obs,
                                            exp.action,
                                            scaled_reward,
                                            None,
                                            exp.step,
                                            exp.episode,
                                            discounted_return)
        return scaled_exp

    def add_experience(self, observation, action, reward, next_observation, step, episode):
        """
        Adds a single step of experience to the two experiences lists
        """
        exp = self.Experience(observation, action, reward, next_observation, step, episode)
        self.experiences.append(exp)

        scaled_exp = self.scale_experience(exp)
        self.scaled_experiences.append(scaled_exp)
        return None

    def process_episode(self, episode_number):

        """
        Calculates the discounted returns

        Inefficient as we loop over the entire episode list.

        TODO some sort of check that episode is actually over
        """
        #  gather the scaled experiences from the last episode
        #  we want to get access to the scaled reward
        episode_experiences, indicies, idx = [], [], 0
        for idx, exp in enumerate(self.scaled_experiences, 0):
            if exp.episode == episode_number:
                episode_experiences.append(exp)
                indicies.append(idx)

        #  we reverse our experience list so we can do an efficient backup
        episode_experiences.reverse()

        #  blank array to hold the returns
        rtns = np.zeros(len(episode_experiences))
        scaled_episode_experiences = []

        for j, exp in enumerate(episode_experiences):

            if j == 0:
                total_return = exp.reward

            else:
                total_return = exp.reward + self.discount_rate * rtns[j-1]

            rtns[j] = total_return

            scaled_exp = self.Scaled_Experience(exp.observation,
                                                exp.action,
                                                exp.reward,
                                                exp.next_observation,
                                                exp.step,
                                                exp.episode,
                                                total_return)

            scaled_episode_experiences.append(scaled_exp)

        #  now we use our original indicies to reindex
        scaled_episode_experiences.reverse()

        for k, idx in enumerate(indicies):
            self.scaled_experiences[idx] = scaled_episode_experiences[k]

        assert len(self.experiences) == len(self.scaled_experiences)

        return None

    def get_random_batch(self, batch_size):
        """
        Gets a random batch of experiences.
        """
        sample_size = min(batch_size, len(self.scaled_experiences))

        #  limiting the scaled_experiences list to the memory length
        memory = self.experiences[-self.memory_length:]
        scaled_memory = self.scaled_experiences[-self.memory_length:]

        assert len(memory) == len(scaled_memory
                                  )
        #  indicies for the batch
        indicies = np.random.randint(low=0,
                                     high=len(memory),
                                     size=sample_size)

        #  randomly sample from the memory & returns
        memory_batch = [memory[i] for i in indicies]
        scaled_memory_batch = [scaled_memory[i] for i in indicies]

        observations = np.array([exp.observation for exp in scaled_memory_batch]).reshape(-1, self.observation_dim)
        actions = np.array([exp.action for exp in memory_batch]).reshape(-1, len(self.action_space))
        returns = np.array([exp.discounted_return for exp in scaled_memory_batch]).reshape(-1, 1)

        assert observations.shape[0] == actions.shape[0]
        assert observations.shape[0] == returns.shape[0]

        return observations, actions, returns

    def get_episode_batch(self, episode_number):
        """
        Gets the experiences for a given episode.

        Quite inefficient as we loop over the entire scaled experiences list.
        """
        scl_episode_experiences = []

        for scl_exp in self.scaled_experiences:
            if scl_exp.episode == episode_number:
                scl_episode_experiences.append(scl_exp)
                assert scl_exp.episode == episode_number

        observations = np.array([exp.observation for exp in scl_episode_experiences]).reshape(-1, len(self.observation_space))
        actions = np.array([exp.action for exp in scl_episode_experiences]).reshape(-1, len(self.action_space))
        returns = np.array([exp.discounted_return for exp in scl_episode_experiences]).reshape(-1, 1)

        assert observations.shape[0] == actions.shape[0]
        assert observations.shape[0] == returns.shape[0]

        return observations, actions, returns
