#!/usr/bin/env python
import functools

##############################################################################
##
## This file is part of Sardana
##
## http://www.tango-controls.org/static/sardana/latest/doc/html/index.html
##
## Copyright 2011 CELLS / ALBA Synchrotron, Bellaterra, Spain
##
## Sardana is free software: you can redistribute it and/or modify
## it under the terms of the GNU Lesser General Public License as published by
## the Free Software Foundation, either version 3 of the License, or
## (at your option) any later version.
##
## Sardana is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU Lesser General Public License for more details.
##
## You should have received a copy of the GNU Lesser General Public License
## along with Sardana.  If not, see <http://www.gnu.org/licenses/>.
##
##############################################################################


"""This module is part of the Python Pool libray. It defines the class for the
trigger/gate generation"""

__all__ = ["PoolTGGeneration", "TGChannel"]

import time
from taurus.core.util.log import DebugIt
from sardana import State
from sardana.pool.pooldefs import SynchDomain
from sardana.pool.poolaction import ActionContext, PoolActionItem, PoolAction 

# The purpose of this class was inspired on the CTAcquisition concept
class TGChannel(PoolActionItem):
    """An item involved in the trigger/gate generation. 
    Maps directly to a trigger object"""

    def __init__(self, trigger_gate, info=None):
        PoolActionItem.__init__(self, trigger_gate)
        if info:
            self.__dict__.update(info)

    def __getattr__(self, name):
        return getattr(self.element, name)


class PoolTGGeneration(PoolAction):
    '''Action class responsible for trigger/gate generation
    '''
    def __init__(self, main_element, name="TGAction"):
        PoolAction.__init__(self, main_element, name)
        self._listener = None

    def add_listener(self, listener):
        self._listener = listener

    def start_action(self, *args, **kwargs):
        '''Start action method. Expects the following kwargs:
        - config - dictionary containing measurement group configuration
        - synchronization - list of dictionaries containing information about
          the expected synchronization
        - moveable (optional)- moveable object used as the synchronization
          source in the Position domain
        - monitor (optional) - counter/timer object used as the synchronization
          source in the Monitor domain
        '''
        cfg = kwargs['config']
        synchronization = kwargs.get('synchronization')
        moveable = kwargs.get('moveable')
        ctrls_config = cfg.get('controllers')
        pool_ctrls = ctrls_config.keys()

        # Prepare a dictionary with the involved channels
        self._channels = channels = {}
        for pool_ctrl in pool_ctrls:
            pool_ctrl_data = ctrls_config[pool_ctrl]
            elements = pool_ctrl_data['channels']

            for element, element_info in elements.items():
                channel = TGChannel(element, info=element_info)
                channels[element] = channel

        # loads generation parameters
        for pool_ctrl in pool_ctrls:
            ctrl = pool_ctrl.ctrl
            pool_ctrl_data = ctrls_config[pool_ctrl]
            elements = pool_ctrl_data['channels']
            for element in elements:
                axis = element.axis
                channel = channels[element]
                ctrl.SetConfiguration(axis, synchronization)
            # TODO: the attaching of the listeners should be done more generic
            # attaching listener to the software trigger gate generator
            if hasattr(ctrl, 'add_listener') and self._listener != None:
                for element in elements:
                    axis = element.axis
                    ctrl.add_listener(axis, self._listener)
                    remove_acq_listener = functools.partial(ctrl.remove_listener,
                                                        axis, self._listener)
                    self.add_finish_hook(remove_acq_listener)
                # TODO: attachment to the position attribute is just a poof of
                # concept. It requires deatach and probably several improvements
                if moveable is not None:
                    position = moveable.get_position_attribute()
                    position.add_listener(ctrl)
                    remove_pos_listener = functools.partial(position.remove_listener,
                                                    ctrl)
                    self.add_finish_hook(remove_pos_listener)
                    for element in elements:
                        axis = element.axis
                        ctrl.subscribe_event(axis, position)
                        unsubscribe_event = functools.partial(ctrl.unsubscribe_event,
                                                    axis, position)
                        self.add_finish_hook(unsubscribe_event)

        with ActionContext(self):
            # PreStartAll on all controllers
            for pool_ctrl in pool_ctrls:
                pool_ctrl.ctrl.PreStartAll()

            # PreStartOne & StartOne on all elements
            for pool_ctrl in pool_ctrls:
                ctrl = pool_ctrl.ctrl
                pool_ctrl_data = ctrls_config[pool_ctrl]
                elements = pool_ctrl_data['channels']
                for element in elements:
                    axis = element.axis
                    channel = channels[element]
                    ret = ctrl.PreStartOne(axis)
                    if not ret:
                        raise Exception("%s.PreStartOne(%d) returns False" \
                                        % (pool_ctrl.name, axis))
                    ctrl.StartOne(axis)

            # set the state of all elements to inform their listeners
            for channel in channels:
                channel.set_state(State.Moving, propagate=2)

            # StartAll on all controllers
            for pool_ctrl in pool_ctrls:
                pool_ctrl.ctrl.StartAll()

    def is_triggering(self, states):
        """Determines if we are triggering or if the triggering has ended
        based on the states returned by the controller(s) and the software
        TG generation.

        :param states: a map containing state information as returned by
                       read_state_info: ((state, status), exception_error)
        :type states: dict<PoolElement, tuple(tuple(int, str), str))
        :return: returns True if is triggering or False otherwise
        :rtype: bool"""
        for elem in states:
            state_info_idx = 0
            state_idx = 0
            state_tggate = states[elem][state_info_idx][state_idx]
            if self._is_in_action(state_tggate):
                return True
        return False

    @DebugIt()
    def action_loop(self):
        '''action_loop method
        '''
        states = {}
        for element in self._channels:
            states[element] = None

        # Triggering loop
        # TODO: make nap configurable (see motion or acquisition loops)
        nap = 0.2
        while True:
            self.read_state_info(ret=states)
            if not self.is_triggering(states):
                break
            time.sleep(nap)

        # Set element states after ending the triggering
        for triggerelement, state_info in states.items():
            with triggerelement:
                triggerelement.clear_operation()
                state_info = triggerelement._from_ctrl_state_info(state_info)
                triggerelement.set_state_info(state_info, propagate=2)
