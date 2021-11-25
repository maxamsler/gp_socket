"""
GP socket communication server class.
Contains the python server side to interface with the (Julia) GP module, for
XRD and with composition spread for binary systems
"""
from crccheck.crc import Crc32, CrcXmodem
from crccheck.checksum import Checksum32
from itertools import compress
import logging
import logging.config
import yaml
import zlib
import binascii
import socket
import struct
import sys
import io
import time
import numpy as np
import copy as cp
import imageio
import PIL.Image as Image
import matplotlib.pyplot as plt
import os
import glob
import PIL.Image as Image
import copy as cp
import time
import numpy as np
import math as mt
import julia
j = julia.Julia()
#j.include("/Users/maxamsler/.julia/config/startup.jl")
#j.include('/Users/maxamsler/Homefolder/PDC/sara-repos/sara/Interface.jl')
#j = julia.Julia(compiled_modules=False)
#j.include("/home/maxamsler/.julia/config/startup.jl")
#j.include('/home/maxamsler/Homefolder/PDC/sara-repo/sara/Interface.jl')
from julia import SARA as Sara
from coefficients import *
from scipy import spatial


with open('logging.yaml', 'r') as f:
    log_cfg = yaml.safe_load(f.read())
logging.config.dictConfig(log_cfg)

class ServerSocket:
    """
    Contains the socket objects for basic operations:
    - open a socket
    - close a socket
    """
    def __init__(self):
        self.logger = logging.getLogger("ServerSocket")
        self.logger.setLevel(logging.INFO)
        self.sock = None
        self.bufflen = 4096 #Buffer length for large data transfers             
        self.host = socket.gethostname()

    def open_socket(self, port):
        """
        Open a TCP/IP socket
        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        #self.server_address = (self.host, port)
        self.server_address = ("localhost", port)
        self.sock.bind(self.server_address)
        self.sock.setblocking(True)
        self.logger.info('Opened socket. Addr: %s, Port: %d', self.server_address[0], self.server_address[1])

    def accept_connection(self):
        """
        Accept TCP/IP connection
        """
        self.sock.listen(1) #Listen only on one at a time
        self.logger.info('Listening on Addr: %s, Port: %d', self.server_address[0], self.server_address[1])
        self.conn, self.addr = self.sock.accept()
        self.logger.info('Established connection. Addr: %s, Port: %d', self.server_address[0], self.server_address[1])
 
    def close_connection(self):
        """
        Close TCP/IP connection
        """
        self.conn.close()
        self.logger.info('Closed connection')

    def close_socket(self):
        """
        Close a TCP/IP socket
        """
        self.sock.close()
        self.logger.info('Closed socket')

class ServerStructProtocol(ServerSocket):
    """
    General structure-based protocol class. 
    Relies on ServerSocket class.
    This class is used to communicate with cameras, spectrometers, XRD, etc.
    and is suited to exchange large amount of data.
    """
    def __init__(self):
        super(ServerStructProtocol, self).__init__()
        self.logger = logging.getLogger("ServerStruct")
        self.crc = True

    def comm_structure_format(self):
        """
        Returns the communication structure format, 
        containing 5 integers of length 32, big endian (if CRC is included),
        and 4 integers without CRC
        The default communication method to send requests.
        0: msg, the message index
        1: msgid, tag to track messages
        2: option, single parameter for enhancing some msg
        3: rc, return code, zero on success, other values depending on request task
        4: data_size, length of data send in following send requests
        5: crc32 (optional), CRC32 checksum of the data to be transmitted in subsequent comm.
        """
        if self.crc:
            s_struct = ">I i i i I I"
        else:
            s_struct = ">I i i i I"
        return s_struct, struct.Struct(s_struct)

    def get_crc(self, data):
        """
        Returns crc32 of the data package
        """
        crc32 = zlib.crc32(data)  & 0xFFFFFFFF 
        return crc32
 
    def pack_data(self, packer, msg):
        """
        Returns the general packed message based on packer.
        """
        values = tuple(msg)
        packed_data = packer.pack(*values)
        return packed_data, values

    def unpack_data(self, unpacker, packed_data):
        """
        Returns the general unpacked message based on unpacker
        """
        unpacked_data = unpacker.unpack(packed_data)
        msg = list(unpacked_data[:])
        return msg

    def comm_pack_data(self, packer, msg):
        """
        Returns the packed message based on packer specifically for the
        communication protocol.
        """
        if len(msg) < 5 or len(msg) > 6:
            self.logger.error("Message length for Struct comm must be exactly 5 or 6, but %d", len(msg))
        values = tuple(msg)
        packed_data = packer.pack(*values)
        return packed_data, values

    def comm_unpack_data(self, unpacker, packed_data):
        """
        Returns the unpacked message based on unpacker specifically for the
        communication protocol.
        """
        unpacked_data = unpacker.unpack(packed_data)
        msg = list(unpacked_data[:])
        return msg

    def comm_send_struct(self, msg_out):
        """
        Sends communication data.
        Wrapper to send data based specifically for the communication protocol.
        """
        if self.crc:
            if len(msg_out) != 6:
                self.logger.error("Message length for Struct comm must be exactly 6, but %d", len(msg_out))
                return
        else:
            if len(msg_out) != 5:
                self.logger.error("Message length for Struct comm must be exactly 5, but %d", len(msg_out))
                return

        s_struct, packer = self.comm_structure_format()
        packed_data, values = self.comm_pack_data(packer, msg_out)
        # Send data
        self.logger.info("Sending "+':'.join([str(i) for i in msg_out]))
        self.logger.debug("Sending %s", binascii.hexlify(packed_data))
        self.conn.sendall(packed_data)

    def comm_recv_struct(self):
        """
        Receivs communication data 
        Wrapper to receive data based specifically for the communication
        protocol.
        Returns the received message.
        """
        s_struct, unpacker = self.comm_structure_format()
        packed_data = self.conn.recv(unpacker.size)
        #packed_data = await loop.sock_recv(self.conn, unpacker.size)
        self.logger.debug("Received %s", binascii.hexlify(packed_data))
        msg_in = self.comm_unpack_data(unpacker, packed_data)
        return msg_in

    def recv_data_buffered(self, unpacker, msg_recv):
        """
        For receiving large amount of data, use this
        buffered transfer protocol
        """
        if msg_recv[4] != unpacker.size:
            self.logger.error("Unpacker size does not match the expected data size %d:%d", msg_recv[4], unpacker.size)
            return
        self.logger.info("Receiving first block")

        packed_data = bytearray(unpacker.size)
        pos = 0
        while pos < unpacker.size:
            cr = self.conn.recv_into(memoryview(packed_data)[pos:])
            if cr == 0:
                raise EOFError
            pos += cr
        amount_received = pos
        ##packed_data = self.sock.recv(min(self.bufflen, unpacker.size))
        ##print(type(packed_data))
        ##self.logger.debug("Received %s", binascii.hexlify(packed_data))
        ##amount_received = len(packed_data)
        ##while amount_received < unpacker.size:
        ##    self.logger.info("Block no x")
        ##    packed_data += self.sock.recv(min(self.bufflen, unpacker.size - amount_received))
        ##    amount_received = len(packed_data)
        ###print("Amount of data expected/received", unpacker.size, amount_received)
        self.logger.info("Received last block")
        if unpacker.size != amount_received:
            self.logger.error("Data loss during transfer of wavelength data %d:%d", unpacker.size, amount_received)
            return
        self.check_crc(packed_data, msg_recv)
        data_recv = self.unpack_data(unpacker, packed_data)
        return data_recv

    def check_msgid(self, msg_id, msg_recv):
        consistent = True
        if msg_id != msg_recv[1]:
            self.logger.error("Message ID inconsistency, %d %d", msg_id, msg_recv[1])
            consistent = False
        return consistent

    def check_crc(self, packed_data, msg_recv):
        consistent = True
        if self.crc:
            crc = self.get_crc(packed_data)
            if crc != msg_recv[5]:
                self.logger.warning("CRC mismatch %d:%d", crc, msg_recv[5])
                consistent = False
        print("Consistency checked")
        return consistent


class ServerGPProtocol(ServerStructProtocol):
    """
    Subclass with functions implemented specifically for the Focus module.
    Relies on the ClientStructProtocol class.
    """
    def __init__(self):
        super(ServerGPProtocol, self).__init__()
        self.exclude = None
        self.single_comp = True
        self.logger = logging.getLogger("ServerGP")
        self.model = None
        # Arrays that hold the current state of the GP
        self.conditions_all = []
        self.gradient_all = np.array([])
        self.gradient_uncert_all = np.array([])
        # set up temperature profile
        self.input_parameters = {}
        self.input_parameters["dT"] = 25 # uncertainty in peak temperature in C
        self.input_parameters["dx"] = 50/1000 # uncertainty in position in mm
        # define the range of temperatures that are taken from each stripe
        self.input_parameters["c_min"] = 10
        self.input_parameters["c_max"] = 200
        self.input_parameters["nout"] = 16
        # σAI
        # stripe uncertainty acquisition function
        self.input_parameters["T_length"] = 30.
        self.input_parameters["tau_length"] = .3
        self.input_parameters["c_length"] = .1 # disregard for systems without composition dimension
        self.setup_gp()

    def setup_gp(self):
        #Setup the initial GP parameter on startup
        # set up temperature profile
        dT = self.input_parameters["dT"]
        dx = self.input_parameters["dx"]
        #self.TProfile = Sara.TemperatureProfile(dT, dx) # TODO: this needs to be updated before the run!
        self.TProfile = Sara.get_temperature_profile_CHESS_Fall_2021(dT, dx) # TODO: this needs to be updated before the run!
        # define the range of temperatures that are taken from each stripe
        c_min, c_max = self.input_parameters["c_min"], self.input_parameters["c_max"]
        nout = self.input_parameters["nout"]
        #self.relevant_T = Sara.get_relevant_T(c_max, c_min, nout)
        self.relevant_T = Sara.get_relevant_T(c_min, c_max, nout)
        # σAI
        # stripe uncertainty acquisition function
        self.policy = Sara.stripe_uncertainty_sampling(self.relevant_T)
        # set up outer GP
        T_length = self.input_parameters["T_length"]
        tau_length = self.input_parameters["tau_length"]
        c_length = self.input_parameters["c_length"] # disregard for systems without composition dimension
        if self.single_comp:
            synth_kernel = Sara.oSARA(T_length, tau_length)
        else:
            synth_kernel = Sara.oSARA(T_length, tau_length, c_length)
        self.GradientModel = Sara.Gaussian(synth_kernel)

    def run_server(self):
        while True:
            self.accept_connection()
            while True:
                try:
                    msg = self.comm_recv_struct()
                    self.logger.info("Received "+':'.join([str(i) for i in msg]))

##        pos_next, m, max_uncertainty, scaler = Sara.innerloop(Xin, Yin, (grid[0], grid[-1]), rescale_parameters = rescale, grid = grid, num_next = num_next)
##        mean, mean_std =  Sara.evaluate(m, np.array(xs).flatten(), scaler)
##        gradients_data = Sara.get_global_data(pos_outer, temp_inner, temp_outer, model, tpeak_domain, scaler)
##        model_gradient = Sara.get_gradient_map(Ts, Logtaus, gradients, domain)
##        Tpeaks_next, Logtaus_next = Sara.outerloop(Tpeaks, Logtaus,
##                model_gradient, grid_Tpeaks, grid_Logtaus, domain, num_next =
##                num_next, alpha = weight)
##
##        Grad_Matrix_Mean, Grad_Matrix_Std = Sara.evaluate_gradient_map(model_gradient, Temp_Matrix, dwell_Matrix, domain)

                    if not msg:
                        break
                    if   msg[0] == 1: 
                        self.get_GP_QUERY_VERSION(msg)
                    elif msg[0] == 2:
                        self.set_GP_SETUP_DIMS(msg)  #All relevant parameters set, currently set on the server only
                    elif msg[0] == 3:
                        self.set_GP_SETUP(msg)       #All relevant parameters set, currently set on the server only
                    elif msg[0] == 4:
                        self.set_GP_XRD_TO_GLOBAL_DIM(msg)
                    elif msg[0] == 5:
                        self.set_GP_XRD_TO_GLOBAL(msg)
                    elif msg[0] == 6:
                        self.set_GP_ALLOWED_COND(msg)
                    elif msg[0] == 7:
                        self.get_GP_NEXT_COND(msg)
                    elif msg[0] == 8:
                        self.set_GP_EXCLUDE_STRIPES(msg)
                    elif msg[0] == 9:
                        self.get_GP_EVALUATE(msg)
                    else:
                        self.logger.error("Function not yet implemented: %d", unpacker.size, msg[0])
                        break
                except:
                    self.logger.warning("Connection lost")
                    self.close_connection()
                    break

    def get_GP_QUERY_VERSION(self, msg_in):
        """
        Returns version of the GP server.
        """
        version = 1234
        msg = cp.deepcopy(msg_in)
        msg[3] = version
        self.comm_send_struct(msg)
        return

    def GP_SETUP_structure_format(self, nknown):
        """ 
        For setting up a new calculation, or for restart
        nknown is the array size for a restart calculation, important for
        fillin the arrays
        self.gradient_all = np.array([])
        self.gradient_uncert_all = ([])
        self.conditions_all = []
        nknown should be multiples of self.input_parameters["nout"]
        if nknown is negative, only the data is sent without changing the
        parameters of length abs(nknown)
        """ 
        # set up temperature profile
        s_struct  = "<"
        if nknown >= 0:
            print("Updating parameters")
            s_struct += "d " # self.input_parameters["dT"] = 25 # uncertainty in peak temperature in C
            s_struct += "d " # self.input_parameters["dx"] = 50/1000 # uncertainty in position in mm
            # define the range of temperatures that are taken from each stripe
            s_struct += "I " # self.input_parameters["c_min"] = 10
            s_struct += "I " # self.input_parameters["c_max"] = 200
            s_struct += "I " # self.input_parameters["nout"] = 16
            # σAI
            # stripe uncertainty acquisition function
            s_struct += "d " # self.input_parameters["T_length"] = 30.
            s_struct += "d " # self.input_parameters["tau_length"] = .3
            s_struct += "d " # self.input_parameters["c_length"] = .1 # disregard for systems without composition dimension
            # Dimensions to be saved from the known arrays for a restart
            # Only add this if restart, i.e., nknown > 0
        else:
            print("Only updating DATA_ALL")
        if abs(nknown) > 0:
            nnknown = abs(nknown)
            print("Only updating DATA", nknown, nnknown)
            s_struct += str(nnknown) + "d " #gradient_all
            s_struct += str(nnknown) + "d " #gradient_uncert_all
            if self.single_comp:
                s_struct += str(2*nnknown) + "d " #conditions_all
            else:
                s_struct += str(3*nnknown) + "d " #conditions_all
        return s_struct, struct.Struct(s_struct)

    def GP_SETUP_structure_todict(self, s_data, nknown):
        """
        Returns the dict from the GP_SETUP_structure_format
        """
        s_dict = {}
        pp = 0
        if nknown >= 0:
            s_dict["dT"] = s_data[pp];          pp += 1
            s_dict["dx"] = s_data[pp];          pp += 1
            s_dict["c_min"] = s_data[pp];       pp += 1
            s_dict["c_max"] = s_data[pp];       pp += 1
            s_dict["nout"] = s_data[pp];        pp += 1
            s_dict["T_length"] = s_data[pp];    pp += 1
            s_dict["tau_length"] = s_data[pp];  pp += 1
            s_dict["c_length"] = s_data[pp];    pp += 1
        if abs(nknown) > 0:
            nnknown = abs(nknown)
            s_dict["gradient_all"] = np.array(s_data[pp:pp+nnknown]);        pp += nnknown
            s_dict["gradient_uncert_all"] = np.array(s_data[pp:pp+nnknown]); pp += nnknown
            if self.single_comp:
                t_data = s_data[pp:pp+nnknown*2]
                it = iter(t_data)
                # zip the iterator with itself
                s_dict["conditions_all"] = list(zip(it, it))
            else:
                t_data = s_data[pp:pp+nnknown*3]
                it = iter(t_data)
                # zip the iterator with itself
                s_dict["conditions_all"] = list(zip(it, it, it))
        return s_dict

    def set_GP_SETUP(self, msg_in):
        """
        Receive the list of touples of allowed conditions
        """
        msg = cp.deepcopy(msg_in)
        msg[3] = 0
        nknown= msg[2]
        r_struct, unpacker = self.GP_SETUP_structure_format(nknown)
        r_data = self.recv_data_buffered(unpacker, msg_in)
        r_dict = self.GP_SETUP_structure_todict(r_data, nknown)
        print(r_dict)
        # Copy the necessary data to self
        # Arrays that hold the current state of the GP
        if nknown == 0:
            self.conditions_all = []
            self.gradient_all = np.array([])
            self.gradient_uncert_all = ([])
        else:
            self.conditions_all      = r_dict["conditions_all"]
            self.gradient_all        = r_dict["gradient_all"]
            self.gradient_uncert_all = r_dict["gradient_uncert_all"]
        if nknown >= 0:
            # set up temperature profile
            self.input_parameters["dT"] = r_dict["dT"]
            self.input_parameters["dx"] = r_dict["dx"]
            # define the range of temperatures that are taken from each stripe
            self.input_parameters["c_min"] = r_dict["c_min"]
            self.input_parameters["c_max"] = r_dict["c_max"]
            self.input_parameters["nout"]  = r_dict["nout"] 
            # σAI
            # stripe uncertainty acquisition function
            self.input_parameters["T_length"]   = r_dict["T_length"]  
            self.input_parameters["tau_length"] = r_dict["tau_length"]
            self.input_parameters["c_length"]   = r_dict["c_length"]  
        self.setup_gp()
        if self.crc:
           msg[5] = 0
        self.comm_send_struct(msg)
        #Set current exclude to None, so all data will be taken into account
        #for the GP model. Set the exclude conditions manually if required for
        #a restart after calling setup!
        self.exclude = None
        print("Reset the GP server to start/restart")
        return

    def GP_XRD_TO_GLOBAL_DIM_structure_format(self):
        """
        Returns the structure format of the array dimensions of the inner
        loop
        """
        s_struct  = "<"
        s_struct += "I "    #Size of x (number of positions)
        s_struct += "I "    #Number of Qvalues (for the Y matrix)
        return s_struct, struct.Struct(s_struct)

    def set_GP_XRD_TO_GLOBAL_DIM(self, msg_in):
        """
        Accepts the inner loop dims and keeps them in self
        """
        msg = cp.deepcopy(msg_in)
        msg[3] = 0
        s_struct, unpacker = self.GP_XRD_TO_GLOBAL_DIM_structure_format()
        packed_data = self.conn.recv(unpacker.size)
        self.check_crc(packed_data, msg_in)
        self.GP_XRD_TO_GLOBAL_DIM = self.unpack_data(unpacker, packed_data)
        if self.crc:
           msg[5] = 0
        self.comm_send_struct(msg)
        return

    def GP_XRD_TO_GLOBAL_structure_format(self, ndim):
        """                                 
        Returns the structure format of sending the XRD maps
        """                                 
        s_struct  = "<"                     
        s_struct += str(ndim[0]) + "d "            #The list of positions x
        s_struct += str(ndim[0] * ndim[1]) + "d "  #The size of the complete xrd map, x times number of Qvalues
        s_struct += str(ndim[1]) + "d "            #Qvalues
        s_struct += "d "                           #T_max
        s_struct += "d "                           #log10_tau
        s_struct += "d "                           #comp
        #Could add bias parameters after
        return s_struct, struct.Struct(s_struct)

    def GP_XRD_TO_GLOBAL_structure_todict(self, s_data, ndims):
        """
        Returns the dict from the GP_XRD_TO_GLOBAL_structure_format
        """
        s_dict = {}
        pp = 0
        s_dict["x"] = np.array(s_data[pp:pp+ndims[0]])
        pp += ndims[0]
        Yin = []
        #s_dict["Y"] = np.array(s_data[pp:pp+ndims[0] * ndims[1]]).reshape((ndims[1], ndims[0]))
        s_dict["Y"] = np.array(s_data[pp:pp+ndims[0] * ndims[1]]).reshape((ndims[0], ndims[1]))
        #for i in range(ndims[1]):
        #    Yin.append(s_dict["Y"][i,:])
        #s_dict["Y"] = np.array(Yin) #Or keep it as list?
        pp += ndims[0] * ndims[1]
        s_dict["Qval"] = np.array(s_data[pp:pp+ndims[1]])
        pp += ndims[1]
        s_dict["T_max"] = s_data[pp]
        pp += 1
        s_dict["log10_tau"] = s_data[pp]
        pp += 1
        s_dict["comp"] = s_data[pp]
#        pp += 1
#        bias_parameters = []
#        for i in range(ndims[2]):
#            bb = (s_data[pp], s_data[pp+1], s_data[pp+2], s_data[pp+3])
#            bias_parameters.append(bb)
#            pp += 4
#        s_dict["rescale"] = bias_parameters
#        s_dict["grid"] = np.array(s_data[pp:pp + ndims[3]])
#        pp += ndims[3]
#        #s_dict["num_next"] = s_data[pp]
        return s_dict

    def GP_ALL_structure_format(self, nknown):
        """ 
        self.gradient_all = np.array([])
        self.gradient_uncert_all = ([])
        self.conditions_all = []
        nknown should be multiples of self.input_parameters["nout"]
        """ 
        # set up temperature profile
        s_struct  = "<"
        if nknown > 0:
            s_struct += str(nknown) + "d " #gradient_all
            s_struct += str(nknown) + "d " #gradient_uncert_all
            if self.single_comp:
                s_struct += str(2*nknown) + "d " #conditions_all
            else:
                s_struct += str(3*nknown) + "d " #conditions_all
        return s_struct, struct.Struct(s_struct)

    def set_GP_XRD_TO_GLOBAL(self, msg_in):
        """
        Receives the positions, the xrd map, T_max, and log10_tau
        Computes the stripe GP and stores the output in self
        """
        msg = cp.deepcopy(msg_in)
        msg[3] = 0
        r_struct, unpacker = self.GP_XRD_TO_GLOBAL_structure_format(self.GP_XRD_TO_GLOBAL_DIM)
        r_data = self.recv_data_buffered(unpacker, msg_in)
        r_dict = self.GP_XRD_TO_GLOBAL_structure_todict(r_data, self.GP_XRD_TO_GLOBAL_DIM)
        print("Received data", r_dict)
        # convert stripe xrd data to global gradient data
        # stripe_to_global(x, y, σ, k, P, T_max, log10_τ, relevant_T, input_noise)
        # x is position
        # Y is matrix of integrated xrd spectrograms as a function of position
        # σ is measurement noise (can be learned using the estimate_noise function)
        self.sigma = 0.05
        self.l     = .2 # length scale of inner GP
        self.rescale_parameters = (1, 0, 0.5, 4 )
        char_kernel = Sara.iSARA(self.l, self.rescale_parameters) # characterization kernel with length scale l
        #char_kernel = Sara.iSARA(self.l) # characterization kernel with length scale l
        # T_max, log10_τ are stripe conditions
        # relevant_T is function defined above
        print(r_dict["Y"].shape)
        if self.single_comp:
            conditions, gradient, gradient_uncert = Sara.xrd_to_global(r_dict["x"],
                                                                   r_dict["Y"].T,
                                                                   self.sigma,
                                                                   char_kernel,
                                                                   self.TProfile,
                                                                   (r_dict["T_max"],
                                                                    r_dict["log10_tau"]),
                                                                   self.relevant_T)
        else:
            conditions, gradient, gradient_uncert = Sara.xrd_to_global(r_dict["x"],
                                                                   r_dict["Y"].T,
                                                                   self.sigma,
                                                                   char_kernel,
                                                                   self.TProfile,
                                                                   (r_dict["T_max"],
                                                                    r_dict["log10_tau"],
                                                                    r_dict["comp"]),
                                                                   self.relevant_T)
        print(type(conditions), type(gradient), type(gradient_uncert))

        self.conditions_all.extend(conditions)
        self.gradient_all = np.append(self.gradient_all, gradient)
        self.gradient_uncert_all = np.append(self.gradient_uncert_all, gradient_uncert)
        # standardize global gradient data before passing it to GP
        y_normalization = np.std(self.gradient_all)
        self.gradient_all /= y_normalization
        self.gradient_uncert_all /= y_normalization**2 # noise variance
        #We send back the updated gradient_all, gradient_uncert_all, and conditions_all
        #back to the client for potential restarts
        nknown = len(self.conditions_all)
        msg[2] = nknown
        data_list  = self.gradient_all.tolist()
        data_list += self.gradient_uncert_all.tolist()
        data_list += [e for l in self.conditions_all for e in l] 
        s_struct, packer = self.GP_ALL_structure_format(nknown)
        packed_data, values = self.pack_data(packer, data_list)
        if self.crc:
           msg[5] = self.get_crc(packed_data)
        msg[3] = len(data_list) #Length of returned array
        msg[4] = packer.size
        self.comm_send_struct(msg)
        self.conn.sendall(packed_data)
        print(len(self.conditions_all), self.gradient_all.shape, self.gradient_uncert_all.shape)
        print(self.conditions_all)
        return 
        
    def GP_COND_structure_format(self, ncond):
        """                                 
        Returns the structure format of sending the XRD maps
        """                                 
        s_struct  = "<"                     
        if self.single_comp:
            s_struct += str(ncond * 2) + "d "  #2 times the number of counditions, which is a touple of T_max, log10_tau, and comp
        else:
            s_struct += str(ncond * 3) + "d "  #3 times the number of counditions, which is a touple of T_max, log10_tau, and comp
        return s_struct, struct.Struct(s_struct)

    def set_GP_ALLOWED_COND(self, msg_in):
        """
        Receive the list of touples of allowed conditions
        """
        msg = cp.deepcopy(msg_in)
        msg[3] = 0
        ncond = msg[2]
        r_struct, unpacker = self.GP_COND_structure_format(ncond)
        r_data = self.recv_data_buffered(unpacker, msg_in)
        it = iter(r_data)
        # zip the iterator with itself
        if self.single_comp:
            self.allowed_conditions = list(zip(it, it))
        else:
            self.allowed_conditions = list(zip(it, it, it))
        if self.crc:
           msg[5] = 0
        self.comm_send_struct(msg)
        return

    def get_GP_NEXT_COND(self, msg_in):
        """
        Return a list of next conditions
        """
        msg = cp.deepcopy(msg_in)
        msg[3] = 0
        next_n_cond = msg[2] #The number of next conditions requested, only 1 for now
        # G is GP
        # x is vector / array of condition tuples
        # y are corresponding gradients (vector of floats)
        # variance is uncertainty in gradients (2nd return value of xrd_to_global)
        # stripe_conditions is vector of potential conditions (tuples)
        # policy is active learning aquisition function
        
        # TODO: Add list of flags for stripes to include or exclude --> Done
        # TODO: To evaluate: Sara.evaluate(self.ConditionalModel, conditions(as # vector of tuples; (temp, dwell, comp))
        # Returns: vector of means and vector of stdvar of same length as input vector

        if self.exclude is None:
            next_cond, self.ConditionalModel = Sara.next_stripe(self.GradientModel, 
                                                         self.conditions_all,
                                                         self.gradient_all,
                                                         self.gradient_uncert_all,
                                                         self.allowed_conditions,
                                                         self.policy)
        else: #Take into account the boolean to exclude certain stripes
            if len(self.exclude_nout) != len(self.conditions_all):
                print("Length of exclude data is not consistent with the model data!", len(self.exclude_nout), len(self.conditions_all))
                print("Not going to exclude anything!")
                self.exclude_nout = [True] * len(self.conditions_all)
            else:
                print("Exclude conditions OK!", len(self.exclude_nout), len(self.conditions_all))
            print("Exclude conditions", len(self.exclude), len(self.exclude_nout), len(self.conditions_all), self.exclude, self.exclude_nout, self.conditions_all) 
            next_cond, self.ConditionalModel = Sara.next_stripe(self.GradientModel, 
                                                         list(compress(self.conditions_all,
                                                                       self.exclude_nout)),
                                                         list(compress(self.gradient_all,
                                                                       self.exclude_nout)),
                                                         list(compress(self.gradient_uncert_all,
                                                                       self.exclude_nout)),
                                                         self.allowed_conditions,
                                                         self.policy)

        data_list = list(next_cond) #.flatten().tolist()
        s_struct, packer = self.GP_COND_structure_format(next_n_cond)
        packed_data, values = self.pack_data(packer, data_list)
        if self.crc:
           msg[5] = self.get_crc(packed_data)
        msg[3] = len(data_list) #Length of returned array
        msg[4] = packer.size
        self.comm_send_struct(msg)
        self.conn.sendall(packed_data)
        return

    def GP_EXCLUDE_STRIPES_structure_format(self, nstripes):
        """                                 
        Returns the structure format of sending the XRD maps
        """                                 
        s_struct  = "<"                     
        s_struct += str(nstripes) + "? "  #number of counditions already annealed
        return s_struct, struct.Struct(s_struct)

    def set_GP_EXCLUDE_STRIPES(self, msg_in):
        """
        Receive the list of boolians to exclude stripes
        """
        msg = cp.deepcopy(msg_in)
        msg[3] = 0
        nstripes = msg[2]
        r_struct, unpacker = self.GP_EXCLUDE_STRIPES_structure_format(nstripes)
        r_data = self.recv_data_buffered(unpacker, msg_in)
        self.exclude = r_data
        self.exclude_nout = []
        for i_exclude in self.exclude:
            self.exclude_nout.extend([i_exclude] * self.input_parameters["nout"])
        print("Excluding stripes", self.exclude)
        print("Excluding nout", self.exclude_nout)
        if self.crc:
           msg[5] = 0
        self.comm_send_struct(msg)
        return

    def GP_EVALUATE_set_structure_format(self, nconds):
        """                                 
        Returns the structure format of sending the evaluation list
        """                                 
        s_struct  = "<"                     
        if self.single_comp:
            s_struct += str(nconds * 2) + "d "  #2 times the number of counditions, which is a touple of T_max, log10_tau
        else:
            s_struct += str(nconds * 3) + "d "  #3 times the number of counditions, which is a touple of T_max, log10_tau, and comp
        return s_struct, struct.Struct(s_struct)

    def GP_EVALUATE_get_structure_format(self, nconds):
        """                                 
        Returns the structure format of sending the means and stds
        """                                 
        s_struct  = "<"                     
        s_struct += str(nconds * 2) + "d "  #2 times the number of counditions, which is a touple of T_max, log10_tau
        return s_struct, struct.Struct(s_struct)

    def get_GP_EVALUATE(self, msg_in):
        """
        Send list of touples of conditions, and return the GP mean and std-dev
        at these conditions
        """
        msg = cp.deepcopy(msg_in)
        nconds = msg[2]
        print("Receiving data to evaluate")
        r_struct, unpacker = self.GP_EVALUATE_set_structure_format(nconds)
        r_data = self.recv_data_buffered(unpacker, msg_in)
        print("GP model on mesh", r_data)
        it = iter(r_data)
        # zip the iterator with itself
        if self.single_comp:
            self.evaluate_conditions = list(zip(it, it))
        else:
            self.evaluate_conditions = list(zip(it, it, it))
        print("Evaluating GP model")
        self.mean, self.std =  Sara.evaluate(self.ConditionalModel, self.evaluate_conditions)
        #Send back the data of the GP model
        data_list = list(self.mean) + list(self.std)
        s_struct, packer = self.GP_EVALUATE_get_structure_format(nconds)
        packed_data, values = self.pack_data(packer, data_list)
        if self.crc:
           msg[5] = self.get_crc(packed_data)
        msg[3] = len(data_list) #Length of returned array
        msg[4] = packer.size
        self.comm_send_struct(msg)
        self.conn.sendall(packed_data)
        return


if __name__ == "__main__":
    gp_server = ServerGPProtocol()
    port = 2300
    gp_server.open_socket(port)
    ### set up temperature profile
    ##dT = 25 # uncertainty in peak temperature in C
    ##dx = 50/1000 # uncertainty in position in mm
    ##gp_server.TProfile = Sara.TemperatureProfile(dT, dx) # TODO: this needs to be updated before the run!
    ### define the range of temperatures that are taken from each stripe
    ##c_min, c_max = 10, 200
    ##nout = 16
    ##gp_server.relevant_T = Sara.get_relevant_T(c_min, c_max, nout)
    ### σAI
    ### stripe uncertainty acquisition function
    ##gp_server.policy = Sara.stripe_uncertainty_sampling(gp_server.relevant_T)
    ### set up outer GP
    ##T_length = 30.
    ##τ_length = .3
    ##c_length = .1 # disregard for systems without composition dimension
    ##synth_kernel = Sara.oSARA(T_length, τ_length, c_length)
    ##gp_server.GradientModel = Sara.Gaussian(synth_kernel)
    
    try:
        gp_server.run_server()

    finally:
        gp_server.close_socket()
