#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2015 - Francesco de Gasperin
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

# Usage: BLavg.py vis.MS
# Load a MS, average visibilities according to the baseline leght,
# i.e. shorter BLs are averaged more, and write a new MS

import os, sys
import optparse
import logging
import numpy as np
from scipy.ndimage.filters import gaussian_filter1d as gfilter
import pyrap.tables as pt
logging.basicConfig(level=logging.DEBUG)

opt = optparse.OptionParser(usage="%prog [options] MS", version="%prog 0.1")
opt.add_option('-i', '--ionfactor', help='Gives an indication on how strong is the ionosphere [default: 0.2]', type='float', default=0.2)
opt.add_option('-o', '--overwrite', help='If active, overwrite the input MS or create a new one [default: False, output is inMS-BLavg.MS]', action="store_true", default=False)
opt.add_option('-c', '--clobber', help='If active, delete the output file if different from input and exists [default: False]', action="store_true", default=False)
opt.add_option('-l', '--column', help='Column name to average, output will always be the same column [default: DATA]', type='string', default='DATA')
(options, msfile_in) = opt.parse_args()
ionfactor = options.ionfactor

if msfile_in == []:
    opt.print_help()
    sys.exit(0)
msfile_in = msfile_in[0]

if not os.path.exists(msfile_in):
    logging.error("Cannot find MS file.")
    sys.exit(1)

# open input MS
if options.overwrite: msin = pt.table(msfile_in, readonly=False, ack=False)
else: msin = pt.table(msfile_in, readonly=True, ack=False)

# prepare output ms
if not options.overwrite:
    msfile_out = msfile_in.replace('.MS','-BLavg.MS')
    if os.path.exists(msfile_out):
        if not options.clobber:
            logging.error("Output file exists and clobber=False")
            sys.exit(1)
        os.system('rm -r '+msfile_out)
    logging.info("Copying MS, this may take a while.")
    os.system('cp -r '+msfile_in+' '+msfile_out)
    logging.info("Copy done.")
    msout = pt.table(msfile_out, readonly=False, ack=False)
        
freqtab = pt.table(msfile_in + '/SPECTRAL_WINDOW', ack=False)
freq = freqtab.getcol('REF_FREQUENCY')
freqtab.close()
wav = 299792458. / freq

# iteration on baselines
for t in msin.iter(["ANTENNA1", "ANTENNA2"]):
    ant1 = t.getcell('ANTENNA1', 0)
    ant2 = t.getcell('ANTENNA2', 0)
    if ant1 >= ant2: continue
    
    # compute the FWHM
    timepersample = t[1]['TIME'] - t[0]['TIME']
    uvw = t.getcol('UVW')
    uvw_dist = np.sqrt(uvw[:, 0]**2 + uvw[:, 1]**2 + uvw[:, 2]**2)
    dist = np.mean(uvw_dist) / 1.e3
    stddev = options.ionfactor * np.sqrt((25.e3 / dist)) * (freq / 60.e6) # in sec
    stddev = stddev/timepersample # in samples
    logging.debug("For BL %i - %i (dist = %.1f km): sigma=%.2f samples." % (ant1, ant2, dist, stddev))

#    Multiply every element of the data by the weights, convolve both the scaled data and the weights, and then
#    divide the convolved data by the convolved weights (translating flagged data into weight=0). That's basically the equivalent of a
#    running weighted average with a Gaussian window function.

    # get weights
    flags = t.getcol('FLAG')
    weights = t.getcol('WEIGHT_SPECTRUM')*~flags # set flagged data weight to 0
    #print 'w', weights.shape
    # get data
    data = t.getcol(options.column)*weights
    #print 'd', data.shape

    # smear weighted data and weights
    dataR = gfilter(np.real(data), stddev, axis=0)#, truncate=4.)
    dataI = gfilter(np.imag(data), stddev, axis=0)#, truncate=4.)
    weights = gfilter(weights, stddev, axis=0)#, truncate=4.)

    # re-create data
    data = (dataR + 1j * dataI)/weights # can I do it?

    # write the BL
    if options.overwrite:
        t.putcol(options.column, data)
        t.putcol('WEIGHT_SPECTRUM', weights)
    else:
        tout = msout.query('ANTENNA1 == '+str(ant1)+' and ANTENNA2 == '+str(ant2))
        tout.putcol(options.column, data)
        tout.putcol('WEIGHT_SPECTRUM', weights)
        tout.close()

msin.close()
if not options.overwrite: msout.close()

logging.info("Done.")