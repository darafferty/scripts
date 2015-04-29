#!/usr/bin/env python
# apply solution from the calibrator and then run selfcal, an initial model
# and initial solutions from a calibrator must be provided
# local dir must contain all the MSs, un touched in linear pol
# 1 selfcal
# 2 subtract field
# 3 flag

# initial self-cal model
model = '/home/fdg/scripts/autocal/VirA_LBA/150328_LBA-VirgoA.model'
# globaldb produced by pipeline-init
globaldb = '../cals/globaldb'
# fake skymodel with pointing direction
fakeskymodel = '/home/fdg/scripts/autocal/VirA_LBA/virgo.fakemodel.skymodel'
# SB per block
n = 20
# max_threads
max_threads = 20

##############################################################

import sys, os, glob, re
from lofar import bdsm
import numpy as np
import lsmtool
import pyrap.tables as pt
from lib_pipeline import *
from make_mask import make_mask

set_logger()

#################################################
# Clear
logging.info('Cleaning...')
check_rm('*log')
check_rm('*last')
check_rm('*h5')
check_rm('concat*MS')
check_rm('block*MS')
check_rm('img')
os.makedirs('img')
check_rm('plot*')

# all MS
mss = sorted(glob.glob('*.MS'))
Nblocks = len(mss)/n
logging.debug("Number of blocks: "+str(Nblocks))
logging.debug("Blocks:")
for j, mss_block in enumerate(np.array_split(mss, Nblocks)):
    logging.debug(str(j)+": "+str(mss_block)+" - len: "+str(len(mss_block)))

##############################################
# Initial processing
logging.info('Fix beam table...')
cmds=[]
for ms in mss:
    cmds.append('/home/fdg/scripts/fixinfo/fixbeaminfo '+ms)
thread_cmd(cmds, max_threads)

#################################################
# Copy cal solution
logging.info('Copy solutions...')
for ms in sorted(mss):
    num = re.findall(r'\d+', ms)[-1]
    check_rm(ms+'/instrument')
    os.system('cp -r '+globaldb+'/sol000_instrument-'+str(num)+' '+ms+'/instrument')

#########################################################################################
# [PARALLEL] apply solutions and beam correction - SB.MS:DATA -> SB.MS:CALCOR_DATA (calibrator corrected data, beam applied, linear)
logging.info('Correcting target MSs...')
cmds=[]
for ms in mss:
    cmds.append('calibrate-stand-alone --replace-sourcedb '+ms+' /home/fdg/scripts/autocal/VirA_LBA/parset_self/bbs-corbeam.parset '+fakeskymodel+' > '+ms+'-init_corbeam.log 2>&1')
thread_cmd(cmds, max_threads)
logging.warning('Bad runs:')
os.system('grep -L "successfully" *-init_corbeam.log')

#########################################################################################
# [PARALLEL] Transform to circular pol - SB.MS:CALCOR_DATA -> SB-circ.MS:CIRC_DATA (data, beam applied, circular)
logging.info('Convert to circular...')
cmds = []
for ms in mss:
    cmds.append('/home/fdg/scripts/mslin2circ.py -i '+ms+':CALCOR_DATA -o '+ms+':CIRC_DATA > '+ms+'-init_circ2lin.log 2>&1')
thread_cmd(cmds, max_threads)
 
# self-cal cycle -> 5
for i in xrange(5):
    logging.info('Starting self-cal cycle: '+str(i))

    # MS for calibration, use all at cycle 0 and 4
    if i == 0 or i == 4:
        mss_c = mss
        mss_clean = mss[::n]
    else:
        mss_c = mss[::n]
        mss_clean = mss[::n]

    if i != 0:    
        model = 'img/clean-c'+str(i-1)+'.model'

    #####################################################################################
    # ft mode, model is unpolarized CIRC == LIN - SB.MS:MODEL_DATA (best m87 model)
    # TODO: test if adding wprojplanes here improves the calibration
    logging.info('Add models...')
    check_rm('concat.MS*')
    pt.msutil.msconcat(mss_c, 'concat.MS', concatTime=False)
    run_casa(command='/home/fdg/scripts/autocal/VirA_LBA/parset_self/casa_ft.py', params={'msfile':'concat.MS', 'model':model}, log='ft-virgo-c'+str(i)+'.log')

    #####################################################################################
    # [PARALLEL] calibrate - SB.MS:CIRC_DATA (no correction)
    # TODO: calibrated on wide-field subtracted data
    logging.info('Calibrate...')
    cmds=[]
    for ms in mss_c:
        cmds.append('NDPPP /home/fdg/scripts/autocal/VirA_LBA/parset_self/NDPPP-selfcal_modeldata.parset msin='+ms+' cal.parmdb='+ms+'/instrument > '+ms+'_selfcal-c'+str(i)+'.log 2>&1')
    thread_cmd(cmds, max_threads)
    logging.warning('Bad runs:')
    os.system('grep -L "Total NDPPP time" *_selfcal_c'+str(i)+'.log')

    #######################################################################################
    # Solution rescaling
    logging.info('Running LoSoTo to normalize solutions...')
    os.makedirs('plot')
    check_rm('globaldb')
    os.makedirs('globaldb')
    for num, ms in enumerate(mss_c):
        os.system('cp -r '+ms+'/instrument globaldb/instrument-'+str(num))
        if num == 0: os.system('cp -r '+ms+'/ANTENNA '+ms+'/FIELD '+ms+'/sky globaldb/')
    h5parm = 'global-c'+str(i)+'.h5'
    os.system('H5parm_importer.py -v '+h5parm+' globaldb > losoto-c'+str(i)+'.log 2>&1')
    os.system('losoto.py -v '+h5parm+' /home/fdg/scripts/autocal/VirA_LBA/parset_self/losoto.parset >> losoto-c'+str(i)+'.log 2>&1')
    os.system('H5parm_exporter.py -v -c '+h5parm+' globaldb >> losoto-c'+str(i)+'.log 2>&1')
    for num, ms in enumerate(mss_c):
        check_rm(ms+'/instrument')
        os.system('mv globaldb/sol000_instrument-'+str(num)+' '+ms+'/instrument')
    os.system('mv plot plot-c'+str(i))

    ########################################################################################
    # [PARALLEL] correct - SB.MS:CIRC_DATA -> SB.MS:CORRECTED_DATA (selfcal corrected data, beam applied, circular)
    logging.info('Correct...')
    cmds=[]
    for ms in mss_c:
        cmds.append('NDPPP /home/fdg/scripts/autocal/VirA_LBA/parset_self/NDPPP-selfcor.parset msin='+ms+' cor.parmdb='+ms+'/instrument > '+ms+'_selfcor-c'+str(i)+'.log 2>&1')
    thread_cmd(cmds, max_threads)
    logging.warning('Bad runs:')
    os.system('grep -L "Total NDPPP time" *_selfcor-c'+str(i)+'.log')

#######################
#   QUICK TEST LOOP
#    # avg - SB.MS:CORRECTED_DATA -> concat-avg.MS:DATA
#    logging.info('Average...')
#    os.system('NDPPP /home/fdg/scripts/autocal/VirA_LBA/parset_self/NDPPP-concatavg.parset msin="['+','.join(mss_clean)+']" msout=concat-avg.MS  > concatavg-c'+str(i)+'.log 2>&1')

    # clean (make a new model of virgo)
#    logging.info('Clean...')
#    run_casa(commnad='/home/fdg/scripts/autocal/VirA_LBA/parset_self/casa_clean.py', params={'msfile':'concat-avg.MS', imagename='img/clean-c'+str(i)}, log='clean-c'+str(i)+'.log')

#    continue
#
######################

    # create widefield model
    if i == 0 or i == 4:

        # concatenate data - MS.MS -> concat.MS (selfcal corrected data, beam applied, circular)
        logging.info('Make widefield model - Concatenating...')
        check_rm('concat.MS*')
        pt.msutil.msconcat(mss_c, 'concat.MS', concatTime=False)

        # uvsub, MODEL_DATA is still Virgo
        logging.info('Make widefield model - UV-Subtracting Virgo A...')
        os.system('taql "update concat.MS set CORRECTED_DATA = CORRECTED_DATA - MODEL_DATA"') # uvsub

        # clean, mask, clean
        logging.info('Make widefield model - Widefield imaging...')
        imagename = 'img/clean-wide-c'+str(i)
        run_casa(commnad='/home/fdg/scripts/autocal/VirA_LBA/parset_self/casa_clean.py', params={'msfile':'concat.MS', imagename=imagename, imtype='wide'}, log='clean-wide1-c'+str(i)+'.log')
        make_mask(image_name = imagename+'.image.tt0', mask_name = imagename+'.newmask')
        run_casa(command='/home/fdg/scripts/autocal/VirA_LBA/parset_self/casa_blank.py', params={'imgs':imagename+'.newmask', 'region':'/home/fdg/scripts/autocal/VirA_LBA/m87.crtf'})
        logging.info('Make widefield model - Widefield imaging2...')
        run_casa(commnad='/home/fdg/scripts/autocal/VirA_LBA/parset_self/casa_clean.py', params={'msfile':'concat.MS', imagename=imagename.reaplce('wide','wode-masked'), mask=imagename+'.newmask' imtype='wide'}, log='clean-wide2-c'+str(i)+'.log')

        # Subtract widefield model using ft on a virtual concat - concat.MS:CORRECTED_DATA -> concat.MS:CORRECTED_DATA-MODEL_DATA (selfcal corrected data, beam applied, circular, field sources subtracted)
        logging.info('Flagging - Subtracting wide-field model...')
        check_rm('concat.MS*')
        pt.msutil.msconcat(mss_c, 'concat.MS', concatTime=False)
        run_casa(command='/home/fdg/scripts/autocal/VirA_LBA/parset_self/casa_ft.py', params={'msfile':'concat.MS', 'model':'img/wide-c'+str(i)+'.model', 'wproj':512}, log='ft-flag-c'+str(i)+'.log')
        os.system('taql "update concat.MS set CORRECTED_DATA = CORRECTED_DATA - MODEL_DATA"') # uvsub

        logging.info('Flagging - Flagging residuals...')
        run_casa(command='/home/fdg/scripts/autocal/VirA_LBA/parset_self/casa_flag.py', params={'msfile':'concat.MS'}, log='flag-c'+str(i)+'.log')

        # [PARALLEL] reapply NDPPP solutions - SB.MS:CIRC_DATA -> SB.MS:CORRECTED_DATA (selfcal corrected data, beam applied, circular)
        # this because with the virtual concat the CORRECTED_DATA have been uvsubbed
        logging.info('Make widefield model - Re-correct...')
        cmds=[]
        for ms in mss_c:
            cmds.append('NDPPP /home/fdg/scripts/autocal/VirA_LBA/parset_self/NDPPP-selfcor.parset msin='+ms+' cor.parmdb='+ms+'/instrument > '+ms+'_selfcor-c'+str(i)+'.log 2>&1')
        thread_cmd(cmds, max_threads)
        logging.warning('Bad runs:')
        os.system('grep -L "Total NDPPP time" *_selfcor-c'+str(i)+'.log')

    # Subtract widefield model using ft on a virtual concat - concat.MS:CORRECTED_DATA -> concat.MS:CORRECTED_DATA-MODEL_DATA (selfcal corrected data, beam applied, circular, field sources subtracted)
    logging.info('Subtracting wide-field model...')
    check_rm('concat.MS*')
    pt.msutil.msconcat(mss_clean, 'concat.MS', concatTime=False)
    run_casa(command='/home/fdg/scripts/autocal/VirA_LBA/parset_self/casa_ft.py', params={'msfile':'concat.MS', 'model':'img/wide-c'+str(i)+'.model', wproj=512}, log='ft-wide-c'+str(i)+'.log')
    os.system('taql "update concat.MS set CORRECTED_DATA = CORRECTED_DATA - MODEL_DATA"') # uvsub

    # avg 1chanSB/20s - SB.MS:CORRECTED_DATA -> concat.MS:DATA (selfcal corrected data, beam applied, circular)
    logging.info('Average...')
    check_rm('concat.MS*')
    os.system('NDPPP /home/fdg/scripts/autocal/VirA_LBA/parset_self/NDPPP-concatavg.parset msin="['+','.join(mss_clean)+']" msout=concat.MS > concatavg-c'+str(i)+'.log 2>&1')

    # clean (make a new model of virgo)
    logging.info('Clean...')
    run_casa(commnad='/home/fdg/scripts/autocal/VirA_LBA/parset_self/casa_clean.py', params={'msfile':'concat.MS', imagename='img/clean-c'+str(i)}, log='clean-c'+str(i)+'.log')

##########################################################################################################
# [PARALLEL] concat+avg - SB.MS:CORRECTED_DATA -> concat.MS:DATA (selfcal corrected data, beam applied, circ)
# TODO: check if it doesn't crash
logging.info('Concat...')
check_rm('concat.MS*')
os.system('NDPPP /home/fdg/scripts/autocal/VirA_LBA/parset_self/NDPPP-concatavg.parset msin="['+','.join(mss)+']" msout=concat.MS > concatavg-c'+str(i)+'.log 2>&1')

#########################################################################################################
# group images of VirA
logging.info('Full BW image...')
run_casa(commnad='/home/fdg/scripts/autocal/VirA_LBA/parset_self/casa_clean.py', params={'msfile':'concat.MS', imagename='img/clean-all'}, log='final_clean-all.log')

#########################################################################################################
# low-res image
logging.info('Make low-resolution image...')
run_casa(commnad='/home/fdg/scripts/autocal/VirA_LBA/parset_self/casa_clean.py', params={'msfile':'concat.MS', imagename='img/clean-lr', imtype='lr'}, log='final_clean-lr.log')

##########################################################################################################
# uvsub + large FoV image
check_rm('concat.MS*')
pt.msutil.msconcat(mss, 'concat.MS', concatTime=False)
logging.info('Ft+uvsub of M87 model...')
run_casa(commnad='/home/fdg/scripts/autocal/VirA_LBA/parset_self/casa_ft.py', params={'msfile':'concat.MS', 'model':'img/clean-c'+str(i)+'.model'}, log='final_ft.log')
os.system('taql "update concat.MS set CORRECTED_DATA = CORRECTED_DATA - MODEL_DATA"') # uvsub

logging.info('Low-res wide field image...')
run_casa(commnad='/home/fdg/scripts/autocal/VirA_LBA/parset_self/casa_clean.py', params={'msfile':'concat.MS', imagename='img/clean-wide', imtype='wide'}, log='final_clean-wide.log')

logging.info("Done.")
