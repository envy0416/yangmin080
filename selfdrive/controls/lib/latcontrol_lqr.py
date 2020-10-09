from selfdrive.ntune import nTune
import numpy as np
from selfdrive.controls.lib.drive_helpers import get_steer_max
from common.numpy_fast import clip,interp,mean
from selfdrive.config import Conversions as CV
from common.realtime import DT_CTRL
from cereal import log


class LatControlLQR():
  def __init__(self, CP):
    #interpolar LQR
    #self.scaleBP = CP.lateralTuning.lqr.scaleBP
    #self.scaleV = CP.lateralTuning.lqr.scaleV

    self.scale = CP.lateralTuning.lqr.scale

    self.ki = CP.lateralTuning.lqr.ki

    self.A = np.array(CP.lateralTuning.lqr.a).reshape((2,2))
    self.B = np.array(CP.lateralTuning.lqr.b).reshape((2,1))
    self.C = np.array(CP.lateralTuning.lqr.c).reshape((1,2))
    self.K = np.array(CP.lateralTuning.lqr.k).reshape((1,2))
    self.L = np.array(CP.lateralTuning.lqr.l).reshape((2,1))
    self.dc_gain = CP.lateralTuning.lqr.dcGain

    self.x_hat = np.array([[0], [0]])
    self.i_unwind_rate = 0.3 * DT_CTRL
    self.i_rate = 1.0 * DT_CTRL

    self.sat_count_rate = 1.0 * DT_CTRL
    self.sat_limit = CP.steerLimitTimer
    self.angle_steers_des = 0.

    self.stoppedSteerAngle = 0.
    self.stoppingSteerAngle = 0.
    self.departingSteerAngle = 0.
    self.stoppingFrame = 0

    self.reset()
    self.tune = nTune(CP, self)


  def reset(self):
    self.i_lqr = 0.0
    self.output_steer = 0.0
    self.sat_count = 0.0

  def _check_saturation(self, control, check_saturation, limit):
    saturated = abs(control) == limit

    if saturated and check_saturation:
      self.sat_count += self.sat_count_rate
    else:
      self.sat_count -= self.sat_count_rate

    self.sat_count = clip(self.sat_count, 0.0, 1.0)

    return self.sat_count > self.sat_limit

  def update(self, active, v_ego, angle_steers, angle_steers_rate, eps_torque, steer_override, rate_limited, CP, path_plan):
    self.tune.check()
    lqr_log = log.ControlsState.LateralLQRState.new_message()

    steers_max = get_steer_max(CP, v_ego)

    # Update Kalman filter
    angle_steers_k = float(self.C.dot(self.x_hat))
    steering_angle = angle_steers
    # Update Kalman filter splitted


    if v_ego < 0.3 or not active:
      lqr_log.active = False
      lqr_output = 0.
      saturated = False
      self.stoppingFrame = 0
      self.stoppedSteerAngle = steering_angle
      self.reset()

    elif v_ego < 2.76 : # about below 10 kmh (2.76666)
      lqr_log.active = False
      lqr_output = 0.
      saturated = False
      self.stoppingFrame = 0
      self.reset()
      self.departingSteerAngle = steering_angle
      if self.stoppingSteerAngle is None :
        self.stoppingSteerAngle = steering_angle

    else:
      torque_scale = (0.45 + v_ego / 60.0)**2  # Scale actuator model with speed
      lqr_log.active = True
      # Subtract offset. Zero angle should correspond to zero torque
      self.angle_steers_des = path_plan.angleSteers - path_plan.angleOffset
      steering_angle -= path_plan.angleOffset

      # Update Kalman filter splitted

      e = steering_angle - angle_steers_k
      self.x_hat = self.A.dot(self.x_hat) + self.B.dot(eps_torque / torque_scale) + self.L.dot(e)
      # Update Kalman filter


      # LQR
      u_lqr = float(self.angle_steers_des / self.dc_gain - self.K.dot(self.x_hat))
      lqr_output = torque_scale * u_lqr / self.scale

      # Integrator
      if steer_override:
        self.i_lqr -= self.i_unwind_rate * float(np.sign(self.i_lqr))
      else:
        error = self.angle_steers_des - angle_steers_k
        i = self.i_lqr + self.ki * self.i_rate * error
        control = lqr_output + i

        if ((error >= 0 and (control <= steers_max or i < 0.0)) or
                (error <= 0 and (control >= -steers_max or i > 0.0))):
          self.i_lqr = i

      self.output_steer = lqr_output + self.i_lqr
      self.output_steer = clip(self.output_steer, -steers_max, steers_max)
      check_saturation = (v_ego > 10) and not rate_limited and not steer_override
      saturated = self._check_saturation(self.output_steer, check_saturation, steers_max)

      if self.stoppingFrame < 75 and  self.stoppingSteerAngle is not None  :
        self.angle_steers_des = mean([self.stoppedSteerAngle,self.stoppingSteerAngle,self.departingSteerAngle])
        self.stoppingFrame = self.stoppingFrame +1
      else :
        if self.stoppingSteerAngle is not None :
          self.stoppingSteerAngle = None


    lqr_log.steerAngle = angle_steers_k + path_plan.angleOffset
    lqr_log.i = self.i_lqr
    lqr_log.output = self.output_steer
    lqr_log.lqrOutput = lqr_output
    lqr_log.saturated = saturated
    return self.output_steer, float(self.angle_steers_des), lqr_log
