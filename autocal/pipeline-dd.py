#!/usr/bin/python
#
# pipeline to do direction dependent calibration using NDPPP-dd

parset_dir = '/home/fdg/scripts/autocal/parset_dd'
maxniter = 10 # max iteration if not converged

##########################################################################################

import sys, os, glob, re
import numpy as np
from autocal.lib_pipeline import *
import pyrap.tables as pt
from make_mask import make_mask
import lsmtool

logger = set_logger('pipeline-dd.logger')
check_rm('logs')
s = Scheduler(dry=False)
mss = sorted(glob.glob('mss/TC*.MS'))
phasecentre = get_phase_centre(mss[0])
check_rm('ddcal/regions')
os.makedirs('ddcal/regions')
check_rm('ddcal/plots')
os.makedirs('ddcal/plots')
check_rm('ddcal/images')
os.makedirs('ddcal/images')
os.makedirs('logs/mss')

def clean(c, mss, size=2.):
    """
    c = cycle/name
    mss = list of mss to avg/clean
    size = in deg of the image
    """
    # set pixscale and imsize
    pixscale = scale_from_ms(mss[0])
    imsize = int(size/(pixscale/3600.)*1.3)

    if imsize < 512:
        imsize = 512

    trim = int(imsize*0.9)

    if imsize % 2 == 1: imsize += 1 # make even
    if trim % 2 == 1: trim += 1 # make even

    logger.debug('Image size: '+str(imsize)+' - Pixel scale: '+str(pixscale))

    # clean 1
    logger.info('Cleaning ('+str(c)+')...')
    imagename = 'img/ddcal-'+str(c)
    s.add('wsclean -reorder -name ' + imagename + ' -size '+str(imsize)+' '+str(imsize)+' -trim '+str(trim)+' '+str(trim)+' \
            -mem 90 -j '+str(s.max_processors)+' -baseline-averaging 2.0 \
            -scale '+str(pixscale)+'arcsec -weight briggs 0.0 -niter 100000 -no-update-model-required -mgain 0.9 -pol I \
            -joinchannels -fit-spectral-pol 2 -channelsout 10 \
            -auto-threshold 20 '+' '.join(mss), \
            log='wsclean-c'+str(c)+'.log', cmd_type='wsclean', processors='max')
    s.run(check=True)

    # make mask
    maskname = imagename+'-mask.fits'
    make_mask(image_name = imagename+'-MFS-image.fits', mask_name = maskname, threshisl = 3)

    # clean 2
    #-multiscale -multiscale-scale-bias 0.5 \
    logger.info('Cleaning w/ mask ('+str(c)+')...')
    imagename = 'img/ddcalM-'+str(c)
    s.add('run_envw.sh wsclean -reorder -name ' + imagename + ' -size '+str(imsize)+' '+str(imsize)+' -trim '+str(trim)+' '+str(trim)+' \
            -mem 90 -j '+str(s.max_processors)+' -baseline-averaging 2.0 \
            -scale '+str(pixscale)+'arcsec -weight briggs 0.0 -niter 1000000 -no-update-model-required -mgain 0.8 -pol I \
            -joinchannels -fit-spectral-pol 2 -channelsout 10 \
            -auto-mask 3 -rms-background-window 40 -rms-background-method rms-with-min -auto-threshold 0.1 \
            -save-source-list -fitsmask '+maskname+' '+' '.join(mss), \
            log='wscleanM-c'+str(c)+'.log', cmd_type='wsclean', processors='max')
    s.run(check=True)
    os.system('cat logs/wscleanM-c'+str(c)+'.log | grep Jy')

    return imagename

############################################################
# Avg to 1 chan/sb
chanband = find_chanband(mss[0])
avg_factor_f = int(np.round(0.2e6/chanband)) # to 1 ch/SB

if avg_factor_f > 1:
    logger.info('Average in freq (factor of %i)...' % avg_factor_f)
    for ms in mss:
        msout = ms.replace('.MS','-avg.MS')
        if os.path.exists(msout): continue
        s.add('NDPPP '+parset_dir+'/NDPPP-avg.parset msin='+ms+' msout='+msout+' msin.datacolumn=CORRECTED_DATA avg.timestep=1 avg.freqstep='+str(avg_factor_f), \
                log=msout.split('/')[-1]+'_avg.log', cmd_type='NDPPP')
    s.run(check=True)
mss = sorted(glob.glob('mss/TC*-avg.MS'))
       
logger.info('Add columns...')
for ms in mss:
    s.add('addcol2ms.py -m '+ms+' -c CORRECTED_DATA,SUBTRACTED_DATA', log=ms+'_addcol.log', cmd_type='python')
s.run(check=True)

###############################################################
#logger.info('BL-based smoothing...')
#for ms in mss:
#    s.add('BLsmooth.py -f 1.0 -r -i DATA -o SMOOTHED_DATA '+ms, log=ms+'_smooth.log', cmd_type='python')
#s.run(check=True)

mosaic_image = sorted(glob.glob('self/images/wide*-[0-9]-MFS-image.fits'))[-1]
os.system('cp self/skymodel.txt ddcal/cat00.txt')
rms_noise_pre = np.inf

for c in xrange(maxniter):
    logger.info('Starting cycle: %i' % c)

    check_rm('img')
    os.makedirs('img')
    os.makedirs('ddcal/images/c'+str(c))

    #############################################################
    # Run pyBDSM to create a model used for DD-calibrator

    logger.info('creating DD skymodel...')
    cat = 'ddcal/cat%02i.txt' % c

    # TODO: remove when fully moved to skymodel
    if not os.path.exists(cat):
        logger.warning('CC catalogue not found, make one with PyBDSM')
        bdsm_img = bdsm.process_image(mosaic_image, rms_box=(100,30), \
            thresh_pix=5, thresh_isl=3, atrous_do=True, atrous_jmax=3, \
            adaptive_rms_box=True, adaptive_thresh=100, rms_box_bright=(30,10), quiet=True)
        bdsm_img.write_catalog(outfile=cat, catalog_type='gaul', bbs_patches='source', format='bbs', clobber=True)

    lsm = lsmtool.load(cat)
    lsm.group('tessellate', targetFlux='20Jy', root='Dir', applyBeam=False, method = 'wmean')
    patches = lsm.getPatchNames()
    logger.info("Created %i directions." % len(patches))

    cat_cl = 'ddcal/cat%02i_cluster.txt' % c
    lsm.write(cat_cl, format='makesourcedb', clobber=True)
    cat_cl_plot = 'ddcal/cat%02i_cluster.png' % c
    lsm.plot(fileName=cat_cl_plot, labelBy='patch')

    # voronoi tessellation of skymodel for imaging
    lsm.group('voronoi', root='Dir', applyBeam=False)
    lsm.setPatchPositions(method='mid') # reset patch center to mid, so phaseshift/imaging is best

    directions_shifts = lsm.getPatchPositions()
    sizes = lsm.getPatchSizes(units='degree')

    cat_voro = 'ddcal/cat%02i_voro.txt' % c
    lsm.write(cat_voro, format='makesourcedb', clobber=True)
    cat_voro_plot = 'ddcal/cat%02i_voro.png' % c
    lsm.plot(fileName=cat_voro_plot, labelBy='patch')
    del lsm

    cat_cl_skydb = cat_cl.replace('.txt','.skydb')
    check_rm(cat_cl_skydb)
    s.add( 'run_env.sh makesourcedb outtype="blob" format="<" in="%s" out="%s"' % (cat_cl, cat_cl_skydb), log='mskesourcedb_cl.log', cmd_type='general' )
    s.run(check=True)

    cat_voro_skydb = cat_voro.replace('.txt','.skydb')
    check_rm(cat_voro_skydb)
    s.add( 'run_env.sh makesourcedb outtype="blob" format="<" in="%s" out="%s"' % (cat_voro, cat_voro_skydb), log='mskesourcedb_voro.log', cmd_type='general')
    s.run(check=True)

    # create masks
    make_voronoi_reg(directions_shifts, mosaic_image, outdir='ddcal/regions/', beam_reg='', png='ddcal/voronoi%02i.png' % c)

    ################################################################
    # Calibration
#    logger.info('Calibrating...')
#    for ms in mss:
#        check_rm(ms+'/cal-c'+str(c)+'.h5')
#        s.add('run_env.sh NDPPP '+parset_dir+'/NDPPP-solDD.parset msin='+ms+' ddecal.h5parm='+ms+'/cal-c'+str(c)+'.h5 ddecal.sourcedb='+cat_cl_skydb, \
#                log=ms+'_solDD-c'+str(c)+'.log', cmd_type='NDPPP')
#    s.run(check=True)
#
#    # Plot solutions
#    # TODO: concat h5parm into a single file
#    logger.info('Running losoto...')
#    for i, ms in enumerate(mss):
#        s.add('losoto -v '+ms+'/cal-c'+str(c)+'.h5 '+parset_dir+'/losoto-plot.parset', log=ms+'_losoto-c'+str(c)+'.log', cmd_type='python', processors='max')
#        s.run(check=True)
#        os.system('mv plots ddcal/plots/plots-c'+str(c)+'-t'+str(i))
#
#    ############################################################
#    # Empty the dataset
#    logger.info('Set SUBTRACTED_DATA = DATA...')
#    for ms in mss:
#        s.add('taql "update '+ms+' set SUBTRACTED_DATA = DATA"', log=ms+'_taql1-c'+str(c)+'.log', cmd_type='general')
#    s.run(check=True)
#
#    logger.info('Subtraction...')
#    for i, p in enumerate(patches):
#        logger.info('Patch '+p+': predict...')
#        for ms in mss:
#            s.add('run_env.sh NDPPP '+parset_dir+'/NDPPP-predict.parset msin='+ms+' pre.sourcedb='+cat_voro_skydb+' pre.sources='+p, log=ms+'_pre1-c'+str(c)+'-p'+str(p)+'.log', cmd_type='NDPPP')
#        s.run(check=True)
#
#        logger.info('Patch '+p+': corrupt...')
#        for ms in mss:
#            s.add('applycal.py --inms '+ms+' --inh5 '+ms+'/cal-c'+str(c)+'.h5 --dir '+str(i)+' --incol MODEL_DATA --outcol MODEL_DATA -c', log=ms+'_cor1-c'+str(c)+'.log', cmd_type='python')
#           # TODO: NDPPP need to support h5parm for correction
#            #s.add('NDPPP '+parset_dir+'/NDPPP-corupt.parset msin='+ms+' cor.parmdb='+ms+'/instrument cor.invert=false', \
#            #    log=ms+'_corrupt-c'+str(c)+'-p'+str(p)+'.log', cmd_type='NDPPP')
#        s.run(check=True)
#
#        logger.info('Patch '+p+': subtract...')
#        for ms in mss:
#            s.add('taql "update '+ms+' set SUBTRACTED_DATA = SUBTRACTED_DATA - MODEL_DATA"', log=ms+'_taql2-c'+str(c)+'-p'+str(p)+'.log', cmd_type='general')
#        s.run(check=True)

    ##############################################################
    # Imaging
    logger.info('Imaging...')

    ## TODO: test
    #logger.info('Empty imaging')
    #s.add('wsclean -reorder -name img/empty-c'+str(c)+' -datacolumn SUBTRACTED_DATA -size 3000 3000 \
    #        -mem 90 -j '+str(s.max_processors)+' -baseline-averaging 2.0 \
    #        -scale 10arcsec -weight briggs 0.0 -niter 0 -no-update-model-required -mgain 1 -pol I '+' '.join(mss), \
    #        log='wscleanEmpty-c'+str(c)+'.log', cmd_type='wsclean', processors='max')
    #s.run(check=True)

    for i, p in enumerate(patches):
        logger.info('Patch '+p+': predict...')
        for ms in mss:
            s.add('run_env.sh NDPPP '+parset_dir+'/NDPPP-predict.parset msin='+ms+' pre.sourcedb='+cat_voro_skydb+' pre.sources='+p, log=ms+'_pre2-c'+str(c)+'-p'+str(p)+'.log', cmd_type='NDPPP')
        s.run(check=True)

        logger.info('Patch '+p+': corrupt...')
        for ms in mss:
            s.add('applycal.py --inms '+ms+' --inh5 '+ms+'/cal-c'+str(c)+'.h5 --dir '+str(i)+' --incol MODEL_DATA --outcol MODEL_DATA -c', log=ms+'_cor1-c'+str(c)+'.log', cmd_type='python')
           # TODO: NDPPP need to support h5parm for correction
            #s.add('NDPPP '+parset_dir+'/NDPPP-corupt.parset msin='+ms+' cor.parmdb='+ms+'/instrument cor.invert=false', \
            #    log=ms+'_corrupt-c'+str(c)+'-p'+str(p)+'.log', cmd_type='NDPPP')
        s.run(check=True)

        logger.info('Patch '+p+': add...')
        for ms in mss:
            s.add('taql "update '+ms+' set CORRECTED_DATA = SUBTRACTED_DATA + MODEL_DATA"', log=ms+'_taql2-c'+str(c)+'-p'+str(p)+'.log', cmd_type='general')
        s.run(check=True)

        # TEST
        #logger.info('Patch '+p+': phase shift and avg...')
        #check_rm('mss_dd')
        #os.makedirs('mss_dd')
        #for ms in mss:
        #    msout = 'mss_dd/'+os.path.basename(ms)
        #    phasecentre = directions_shifts[p]
        #    s.add('NDPPP '+parset_dir+'/NDPPP-shiftavg.parset msin='+ms+' msout='+msout+' shift.phasecenter=['+str(phasecentre[0].degree)+'deg,'+str(phasecentre[1].degree)+'deg\]', \
        #        log=ms+'_shift-c'+str(c)+'-p'+str(p)+'.log', cmd_type='NDPPP')
        #s.run(check=True)
        #logger.info('Patch '+p+': corrupted image...')
        #clean('uncor-'+p, glob.glob('mss_dd/*MS'), size=sizes[i]) # TEST
        # end TEST

        logger.info('Patch '+p+': correct...')
        for ms in mss:
            s.add('applycal.py --inms '+ms+' --inh5 '+ms+'/cal-c'+str(c)+'.h5 --dir '+str(i)+' --incol CORRECTED_DATA --outcol CORRECTED_DATA', log=ms+'_cor2-c'+str(c)+'.log', cmd_type='python')
            # TODO: NDPPP need to support h5parm for correction
            #s.add('NDPPP '+parset_dir+'/NDPPP-corupt.parset msin='+ms+' cor.parmdb='+ms+'/instrument cor.invert=false', \
            #     log=ms+'_corrupt-c'+str(c)+'-p'+str(p)+'.log', cmd_type='NDPPP')
        s.run(check=True)

        logger.info('Patch '+p+': phase shift and avg...')
        check_rm('mss_dd')
        os.makedirs('mss_dd')
        for ms in mss:
            msout = 'mss_dd/'+os.path.basename(ms)
            phasecentre = directions_shifts[p]
            s.add('NDPPP '+parset_dir+'/NDPPP-shiftavg.parset msin='+ms+' msout='+msout+' shift.phasecenter=['+str(phasecentre[0].degree)+'deg,'+str(phasecentre[1].degree)+'deg\]', \
                log=ms+'_shift-c'+str(c)+'-p'+str(p)+'.log', cmd_type='NDPPP')
        s.run(check=True)

        logger.info('Patch '+p+': imageing...')
        clean(p, glob.glob('mss_dd/*MS'), size=sizes[i])

    ##############################################################
    # Mosaiching
    logger.info('Mosaic: image...')
    mosaic_image = 'img/mos_image.fits'
    images = ' '.join(sorted(glob.glob('img/ddcalM-Dir*MFS-image.fits')))
    masks = ' '.join(sorted(glob.glob('ddcal/regions/Dir*reg')))
    s.add('mosaic.py --image '+images+' --masks '+masks+' --output '+mosaic_image, log='mosaic-img-c'+str(c)+'.log', cmd_type='python')
    s.run(check=True)

    logger.info('Mosaic: residuals...')
    images = ' '.join(sorted(glob.glob('img/ddcalM-Dir*MFS-residual.fits')))
    masks = ' '.join(sorted(glob.glob('ddcal/regions/Dir*reg')))
    mosaic_residual = 'img/mos_residual.fits'
    s.add('mosaic.py --image '+images+' --masks '+masks+' --output '+mosaic_image, log='mosaic-res-c'+str(c)+'.log', cmd_type='python')
    s.run(check=True)

    os.system('cp img/*M*MFS-image.fits img/mos_image.fits img/mos_residual.fits ddcal/images/c'+str(c))

    # prepare new skymodel
    skymodels = glob.glob('img/ddcalM-*-sources.txt')
    lsm = lsmtool.load(skymodels[0])
    lsm.ungroup() # necessary remove old patches? are there any of them?
    for skymodel in skymodels[1:]:
        lsm2 = lsmtool.load(skymodel)
        lsm2.ungroup() # necessary remove old patches? are there any of them?
        lsm.concatenate(lsm2)
    lsm.write('ddcal/cat%02i.txt' % c+1, format='makesourcedb', clobber=True)

    # get noise, if larger than 95% of prev cycle: break
    rms_noise = get_noise_img(mosaic_residual)
    logger.info('RMS noise: %f' % rms_noise)
    if rms_noise > 0.95 * rms_noise_pre: break
    rms_noise_pre = rms_noise