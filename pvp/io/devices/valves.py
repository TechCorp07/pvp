from abc import ABC, abstractmethod
from pvp.io.devices.pins import Pin, PWMOutput
import os

import numpy as np
from pvp.common.loggers import init_logger


class SolenoidBase(ABC):
    """ An abstract baseclass that defines methods using valve terminology.
    Also allows configuring both normally _open and normally closed valves (called the "form" of the valve).
    """
    _FORMS = {'Normally Closed': 0,
              'Normally Open': 1}

    def __init__(self, form='Normally Closed'):
        """

        Args:
            form (str): The form of the solenoid; can be either `Normally Open` or `Normally Closed`
        """
        self.form = form
        self.logger = init_logger(__name__)

    @property
    def form(self) -> str:
        """ Returns the human-readable form of the valve."""
        return dict(map(reversed, self._FORMS.items()))[self._form]

    @form.setter
    def form(self, form):
        """ Performs validation on requested form and then sets it.

        Args:
            form (str): The form of the solenoid; can be either `Normally Open` or `Normally Closed`
        """
        if form not in self._FORMS.keys():
            raise ValueError('form must be one of {}'.format(self._FORMS.keys()))
        else:
            self._form = self._FORMS[form]

    @abstractmethod
    def open(self):
        """ Energizes valve if Normally Closed. De-energizes if Normally Open."""

    @abstractmethod
    def close(self):
        """ De-energizes valve if Normally Closed. Energizes if Normally Open."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """ Returns True if valve is open, False if it is closed"""


class OnOffValve(SolenoidBase, Pin):
    """ An extension of pvp.io.iobase.Pin which uses valve terminology for its methods.
    Also allows configuring both normally _open and normally closed valves (called the "form" of the valve).
    """
    _FORMS = {'Normally Closed': 0,
              'Normally Open': 1}

    def __init__(self, pin, form='Normally Closed', pig=None):
        """

        Args:
            pin (int): The number of the pin to use
            form (str): The form of the solenoid; can be either `Normally Open` or `Normally Closed`
            pig (PigpioConnection): pigpiod connection to use; if not specified, a new one is established
        """
        self.form = form
        Pin.__init__(self, pin, pig)
        SolenoidBase.__init__(self, form=form)

    def open(self):
        """ Energizes valve if Normally Closed. De-energizes if Normally Open."""
        if self._form:
            self.write(0)
        else:
            self.write(1)

    def close(self):
        """ De-energizes valve if Normally Closed. Energizes if Normally Open."""
        if self.form == 'Normally Closed':
            self.write(0)
        else:
            self.write(1)

    @property
    def is_open(self) -> bool:
        """ Implements parent's abstractmethod; returns True if valve is open, False if it is closed"""
        energized = True if self.read() else False
        if self.form == 'Normally Closed':
            return energized
        else:
            return not energized


class PWMControlValve(SolenoidBase, PWMOutput):
    """ An extension of PWMOutput which incorporates linear
    compensation of the valve's response.
    """

    def __init__(self, pin, form='Normally Closed', frequency=None, response=None, pig=None):
        """
        Args:
            pin (int): The number of the pin to use
            form (str): The form of the solenoid; can be either `Normally Open` or `Normally Closed`
            frequency (float): The PWM frequency to use.
            response (str): "/path/to/response/curve/file"
            pig (PigpioConnection): pigpiod connection to use; if not specified, a new one is established
        """
        PWMOutput.__init__(self, pin=pin, initial_duty=0, frequency=frequency, pig=pig)
        SolenoidBase.__init__(self, form=form)
        '''if response is None:
            raise NotImplementedError('You need to implement a default response behavior')'''
        if form != 'Normally Closed':
            raise NotImplementedError('Normally Open PWM control valves have not been implemented')
        self._rising = True
        self._load_valve_response(response_path=response)

    @property
    def is_open(self) -> bool:
        """ Implements parent's abstractmethod; returns True if valve is open, False if it is closed"""
        if self.setpoint > 0:
            return True
        else:
            return False

    def open(self):
        """ Implements parent's abstractmethod; fully opens the valve"""
        self.setpoint = 1.0

    def close(self):
        """ Implements parent's abstractmethod; fully closes the valve"""
        self.setpoint = 0.0

    @property
    def setpoint(self) -> float:
        """ The linearized setpoint corresponding to the current duty cycle according to the valve's response curve

        Returns:
            float: A number between 0 and 1 representing the current flow as a proportion of maximum
        """
        return self.inverse_response(self.duty, self._rising)

    @setpoint.setter
    def setpoint(self, setpoint):
        """Overridden to determine & write the duty cycle corresponding
        to the requested linearized setpoint according to the valve's
        response curve

        Args:
            setpoint (float): A number between 0 and 100 representing how much to open the valve
        """
        if not 0 <= setpoint <= 100:
            raise ValueError('setpoint must be between 0 and 100 for an expiratory control valve')
        self._rising = setpoint > self.setpoint
        self.duty = self.response(setpoint, self._rising)

    def response(self, setpoint, rising=True):
        """Setpoint takes a value in the range (0,100) so as not to confuse with duty cycle, which takes a value in the
        range (0,1). Response curves are specific to individual valves and are to be implemented by subclasses.
        Different curves are calibrated to 'rising = True' (valves opening) or'rising = False' (valves closing), as
        different characteristic flow behavior can be observed.

        Args:
            setpoint (float): A number between 0 and 1 representing how much to open the valve
            rising (bool): Whether or not the requested setpoint is higher than the last (rising = True), or the
                opposite (Rising = False)

        Returns:
            float: The PWM duty cycle corresponding to the requested setpoint
        """

        idx = (np.abs(self._response_array[:, 0] - setpoint)).argmin()
        if rising:
            duty = self._response_array[idx, 1]
        else:
            duty = self._response_array[idx, 2]

        return duty

    def inverse_response(self, duty_cycle, rising=True):

        """Inverse of response. Given a duty cycle in the range (0,1), returns the corresponding linear setpoint in the
        range (0,100).

        Args:
            duty_cycle: The PWM duty cycle
            rising (bool): Whether or not the requested setpoint is higher than the last (rising = True), or the
                opposite (Rising = False)

        Returns:
            float: The setpoint of the valve corresponding to `duty_cycle`
        """
        if rising:
            idx = (np.abs(self._response_array[:, 1] - duty_cycle)).argmin()
        else:
            idx = (np.abs(self._response_array[:, 2] - duty_cycle)).argmin()
        return self._response_array[idx, 0]

    def _load_valve_response(self, response_path):
        """ Loads and applies a response curve of the form `f(setpoint) = duty`. A response curve maps the underlying
        PWM duty cycle `duty` onto the normalized variable `setpoint` representing the flow through the valve as a
        percentage of its maximum.

        Flow through a proportional valve may be nonlinear with respect to [PWM] duty cycle, if the valve itself does
        not include its own electronics to linearize response wrt/ input. Absent on-board compensation of response, a
        proportional solenoid with likely not respond [flow] at all below some minimum threshold duty cycle.
        Above this threshold, the proportional valve begins to open and its response resembles a sigmoid: just past the
        threshold there is a region where flow increases exponentially wrt/ duty cycle, this is followed by a region of
        pseudo-linear response that begins to taper off, eventually approaching the valve's maximum flow asymptotically
        as the duty cycle approaches 100% and the valve opens fully.

        Args:
            response_path: 'path/to/binary/response/file' - if response_path is None, defaults to `setpoint = duty`
        """
        if response_path is not None:
            if os.path.isabs(response_path):
                response_array = np.load(response_path)
            else:
                # relative path, check if it's relative to the root of the package (ie. starts with pvp/)
                base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
                combined_path = os.path.join(base_dir, response_path)
                if os.path.exists(combined_path):
                    response_array = np.load(combined_path)
                else:
                    # if it still can't be found, try loading it as given and otherwise give informative error message
                    try:
                        response_array = np.load(response_path)
                    except FileNotFoundError as e:
                        print(e)
                        raise FileNotFoundError(f'Could not find valve response calibration file, path must either be an absolute path or a path relative to the directory containing the package (eg. pvp/io/config/calibration/...). Given {response_path}')

        else:
            self.logger.exception('Response path was not passed, generating a linear response curve')
            response_array = np.linspace([0, 0, 0], [100, 1, 1], num=101)
        self._response_array = response_array


class SimOnOffValve(SolenoidBase):
    """ stub: a simulated on/off valve"""

    def __init__(self, pin=None, form='Normally Closed', pig=None):
        super().__init__(form=form)
        self.state = 0 if form == 'Normally Closed' else 1

    def open(self):
        self.state = 1

    def close(self):
        self.state = 0

    @property
    def is_open(self) -> bool:
        return True if self.state == 1 else False


class SimControlValve(SolenoidBase):
    """stub: a simulated linear control valve"""

    def __init__(self, pin=None, form='Normally Closed', frequency=None, response=None, pig=None):
        """
        Args:
            pin (int): (unused for sim)
            form (str): The form of the solenoid; can be either `Normally Open` or `Normally Closed`
            frequency (float): (unused for sim)
            response (str): (unused for sim) # TODO implement this (requires refactor)
            pig (PigpioConnection): (unused for sim)
        """
        if response:
            raise NotImplementedError('This sim is pretty basic - no fancy response for you')
        if form != 'Normally Closed':
            raise NotImplementedError('Normally Open sim control valves have not been implemented')
        super().__init__(form=form)
        self._setpoint = 0

    @property
    def is_open(self) -> bool:
        """ Implements parent's abstractmethod; returns True if valve is open, False if it is closed
        FIXME: Needs refactor; duplicate property to PWMControlValve.is_open"""

        if self.setpoint > 0:
            return True
        else:
            return False

    def open(self):
        """ Implements parent's abstractmethod; fully opens the valve
        FIXME: Needs refactor; duplicate method to PWMControlValve.open()"""
        self.setpoint = 100

    def close(self):
        """ Implements parent's abstractmethod; fully closes the valve
        FIXME: Needs refactor; duplicate method to PWMControlValve.close()"""
        self.setpoint = 0

    @property
    def setpoint(self):
        """ The requested linearized set-point of the valve.

        Returns:
            float: A number between 0 and 1 representing the current flow as a proportion of maximum
        """
        return self._setpoint

    @setpoint.setter
    def setpoint(self, setpoint):
        """
        Args:
            setpoint (float): Between 0 and 100; the requested set-point of the valve as a proportion of maximum
        """
        if not 0 <= setpoint <= 100:
            raise ValueError('setpoint must be between 0 and 100 for an expiratory control valve')
        self._setpoint = setpoint
