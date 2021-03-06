import collections
import decimal
import os

import numpy as np

from energy_py.envs.env_ts import Time_Series_Env
from energy_py.main.scripts.spaces import Continuous_Space, Discrete_Space
from energy_py.main.scripts.visualizers import Env_Episode_Visualizer
from energy_py.main.scripts.utils import ensure_dir

class Battery_Visualizer(Env_Episode_Visualizer):
    """
    A class to create charts for the Battery environment.
    """

    def __init__(self, env_info, state_ts, episode):
        super().__init__(env_info, state_ts, episode)

    def _output_results(self):
        """
        The main visulizer function
        """
        #  make the main dataframe
        self.outputs['dataframe'] = self.make_dataframe()
        #  print out some results
        self.print_results()

        def make_technical_fig(env_outputs_df, env_outputs_path):
            fig = self.make_figure(df=env_outputs_df,
                                   cols=['rate', 'new_charge'],
                                   xlabel='Time',
                                   ylabel='Electricity [MW or MWh]',
                                   path=os.path.join(env_outputs_path, 'technical_fig_{}.png'.format(self.episode)))
            return fig

        self.figures = {'technical_fig':make_technical_fig}
        return self.outputs


class Battery_Env(Time_Series_Env):
    """
    An environment that simulates storage of electricity in a battery.
    Agent chooses to either charge or discharge.

    Args:
        lag                     (int)   : lag between observation & state


        episode_length          (int)   : length of the episode
                                (string): 'maximum' = run entire legnth
        episode_start           (int)   : the integer index to start the episode

        power_rating            (float) : maximum rate of battery charge or discharge [MWe]
        capacity                (float) : amount of electricity that can be stored [MWh]
        round_trip_eff          (float) : round trip efficiency of storage
        initial_charge          (float) : inital amount of electricity stored [MWh]

        verbose                 (int)   : controls env print statements
    """
    def __init__(self, lag,
                       episode_length,
                       episode_start,
                       power_rating,

                       capacity,
                       round_trip_eff = 0.9,
                       initial_charge = 0,

                       episode_visualizer = Battery_Visualizer,

                       verbose = 0):

        import os
        path = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(path, 'state.csv')
        print(path)

        self.csv_path = path

        #  calling init method of the parent Time_Series_Env class
        super().__init__(episode_visualizer, lag, episode_length, episode_start, self.csv_path, verbose)

        #  technical energy inputs
        self.power_rating   = float(power_rating)
        self.capacity       = float(capacity)
        self.round_trip_eff = float(round_trip_eff)
        self.initial_charge = float(initial_charge)

        #  resetting the environment
        self.observation    = self.reset()

    def _reset(self):
        """
        Resets the environment.
        """

        """
        SETTING THE ACTION SPACE

        two actions
         1 -  how much to charge [MWh]
         2 -  how much to discharge [MWh]

        use two actions to keep the action space positive
        is useful for policy gradient where we take log(action)
        """
        self.action_space = [Continuous_Space(low  = 0,
                                              high = self.power_rating),
                             Continuous_Space(low  = 0,
                                              high = self.power_rating)]

        """
        SETTING THE OBSERVATION SPACE

        the observation space is set in the parent class Time_Series_Env
        we also append on an additional observation of the battery charge
        """
        self.observation_space, self.observation_ts, self.state_ts = self.ts_env_main()

        self.observation_space.append(Continuous_Space(0, self.capacity))

        """
        SETTING THE REWARD SPACE

        minimum reward = minimum electricity price * max rate of discharge
        maximum reward = maximum electricity price * max rate of discharge

        we also use the peak customer demand
        """

        min_price = self.raw_ts.loc[:, 'C_electricity_price_[$/MWh]'].min()
        min_price = self.raw_ts.loc[:, 'C_electricity_price_[$/MWh]'].max()
        peak_customer_demand = self.state_ts.loc[:, 'C_electricity_demand_[MW]'].max()
        peak_demand = self.power_rating + peak_customer_demand
        self.reward_space = Continuous_Space((-2000 * peak_demand)/12,
                                             (14000 * peak_demand)/12)

        #  reseting the step counter, state, observation & done status
        self.steps = 0
        self.state = self.get_state(steps=self.steps, append=self.initial_charge)
        self.observation = self.get_observation(steps=self.steps, append=self.initial_charge)
        self.done  = False

        initial_charge = self.state[-1]
        assert initial_charge <= self.capacity
        assert initial_charge >= 0

        #  resetting the info & outputs dictionaries
        self.info = collections.defaultdict(list)
        self.outputs = collections.defaultdict(list)

        return self.observation

    def _step(self, action):
        """
        Args:
            action (np.array)         :
            where - action[0] (float) : rate to charge this time step
            where - action[1] (float) : rate to discharge this time step

            TODO needs protection against zero demand
        """

        #  setting the decimal context
        #  make use of decimal so that that the energy balance works
        #  had floating point number issues when always using floats
        #  room for improvement here!
        decimal.getcontext().prec = 6

        #  pulling out the state infomation
        electricity_price = self.state[0]
        electricity_demand = self.state[1]
        old_charge = decimal.Decimal(self.state[-1])

        #  checking the actions are valid
        for i, act in enumerate(action):
            assert act >= self.action_space[i].low
            assert act <= self.action_space[i].high

        #  calculate the net effect of the two actions
        #  also convert from MW to MWh/5 min by /12
        net_charge = float(action[0] - action[1]) / 12
        net_charge = decimal.Decimal(net_charge)

        #  we first check to make sure this charge is within our capacity limits
        unbounded_new_charge = old_charge + net_charge
        bounded_new_charge = max(min(unbounded_new_charge, decimal.Decimal(self.capacity)), decimal.Decimal(0))

        #  now we check to see this new charge is within our power rating
        #  note the * 12 is to convert from MWh/5min to MW
        #  here I am assuming that the power_rating is independent of charging/discharging
        unbounded_rate = (bounded_new_charge - old_charge) * 12
        rate = max(min(unbounded_rate, self.power_rating), -self.power_rating)

        #  finally we account for round trip efficiency
        losses = 0
        gross_rate = decimal.Decimal(rate)

        if gross_rate > 0:
            losses = gross_rate * (1 - decimal.Decimal(self.round_trip_eff)) / 12

        new_charge = old_charge + gross_rate / 12 - losses
        net_stored = new_charge - old_charge
        rate = net_stored * 12

        # TODO more work on balances
        #  set a tolerance for the energy balances
        tolerance = 1e-4

        assert (new_charge) - (old_charge + net_stored) < tolerance
        assert (rate) - (12 * net_stored) < tolerance

        #  we then change our rate back into a floating point number
        rate = float(rate)

        #  calculate the business as usual cost
        #  BAU depends on
        #  - site demand
        #  - electricity price
        BAU_cost = (electricity_demand / 12) * electricity_price

        #  now we can calculate the reward
        #  reward depends on both
        #  - how much electricity the site is demanding
        #  - what our battery is doing (on a gross basis!)
        #  - electricity price
        adjusted_demand = electricity_demand + float(gross_rate)
        RL_cost = (adjusted_demand / 12) * electricity_price
        reward = -RL_cost

        if self.verbose > 0:
            print('step is {}'.format(self.steps))
            print('action was {}'.format(action))
            print('old charge was {}'.format(old_charge))
            print('new charge is {}'.format(new_charge))
            print('rate is {}'.format(rate))
            print('losses were {}'.format(losses))

        #  check to see if episode is done
        #  -1 in here because of the zero index
        if self.steps == (self.episode_length-1):
            self.done = True
            next_state = False
            next_observation = False
            reward = 0
            print('Episode {} finished'.format(self.episode))

        else:
        #  moving onto next step
            next_state = self.get_state(self.steps, append=float(new_charge))
            next_observation = self.get_observation(self.steps, append=float(new_charge))
            self.steps += int(1)

        #  saving info
        self.info = self.update_info(episode            = self.episode,
                                     steps              = self.steps,
                                     state              = self.state,
                                     observation        = self.observation,
                                     action             = action,
                                     reward             = reward,
                                     next_state         = next_state,
                                     next_observation   = next_observation,

                                     BAU_cost           = BAU_cost,
                                     RL_cost            = RL_cost,

                                     electricity_price  = electricity_price,
                                     electricity_demand = electricity_demand,
                                     rate               = rate,
                                     losses             = losses,
                                     adjusted_demand    = adjusted_demand,
                                     new_charge         = new_charge,
                                     old_charge         = old_charge,
                                     net_stored         = net_stored)

        #  moving to next time step
        self.state = next_state
        self.observation = next_observation

        return self.observation, reward, self.done, self.info

    def update_info(self, episode,
                          steps,
                          state,
                          observation,
                          action,
                          reward,
                          next_state,
                          next_observation,

                          BAU_cost,
                          RL_cost,

                          electricity_price,
                          electricity_demand,
                          rate,
                          losses,
                          adjusted_demand,
                          new_charge,
                          old_charge,
                          net_stored):
        """
        helper function to updates the self.info dictionary
        """
        self.info['episode'].append(episode)
        self.info['steps'].append(steps)
        self.info['state'].append(state)
        self.info['observation'].append(observation)
        self.info['action'].append(action)
        self.info['reward'].append(reward)
        self.info['next_state'].append(next_state)
        self.info['next_observation'].append(next_observation)

        self.info['BAU_cost_[$/5min]'].append(BAU_cost)
        self.info['RL_cost_[$/5min]'].append(RL_cost)

        self.info['electricity_price'].append(electricity_price)
        self.info['electricity_demand'].append(electricity_demand)
        self.info['rate'].append(rate)
        self.info['losses'].append(losses)
        self.info['adjusted_demand'].append(adjusted_demand)
        self.info['new_charge'].append(new_charge)
        self.info['old_charge'].append(old_charge)
        self.info['net_stored'].append(net_stored)

        return self.info
