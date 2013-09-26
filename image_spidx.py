#!/usr/bin/python

import sys
import optparse
import pyrap.images
import pyrap.images.coordinates
import numpy as np
import math

# solve memory leak
import matplotlib
matplotlib.use('Agg')
from matplotlib.cbook import report_memory
import pylab

def f(B, x):
      return B[0]*x + B[1]

# the errors are not estimatable...
def linear_fit_fast(x, y, yerr=None):
    from scipy.optimize import leastsq
    if yerr == None: yerr = np.ones(len(y))
    for i,e in enumerate(yerr):
	    if e == 0: yerr[i] = 1
    def errf(B, x, y, yerr):
	    return f(B,x)-y/yerr
    out = leastsq(errf, [-1. ,0.], args=(x, y, yerr), full_output=1)
    return (out[0][0], out[0][1], 0, 0)

def linear_fit(x, y, xerr=None, yerr=None):
    from scipy import odr
    linear = odr.Model(f)
    if xerr == None: xerr = np.ones(len(x))
    if yerr == None: yerr = np.ones(len(y))
    for i,e in enumerate(yerr):
	    if e == 0: yerr[i] = 1
    mydata = odr.Data(x, y, wd=1/xerr, we=1/yerr)
    myodr = odr.ODR(mydata, linear, beta0=[-1., 0.])
    myoutput = myodr.run()
    return(myoutput.beta[0],myoutput.beta[1],myoutput.sd_beta[0],myoutput.sd_beta[1])

# if values is > 5 sigma for each map then OK, otherwise do not perform regression
def badrms(values, rmsvalues):
  for t in xrange(len(values)):
    if (values[t] < 5*rmsvalues[t]): 
      return False # TURN TO TRUE TO ACTIVATE
  return False

opt = optparse.OptionParser(usage="%prog images", version="%prog 0.3")
opt.add_option('-o', '--outimg', help='Output spctral index image [default = spidx.img]', default='spidx.img')
opt.add_option('-m', '--maskimg', help='Mask image tells image_spidx.py where perform linear regression')
opt.add_option('-p', dest='doplot', action='store_true', help='Create 2D (freq Vs lum) plots of selected pixels (in ./plots dir)', default=False)
opt.add_option('-a', '--maskplot', help='Mask plot tells image_spidx.py where perform 2D plots [-p must be active!]')
opt.add_option('-z', dest='dozone', action="store_true", help='xels in the maskplot zone are averaged and a single regression plot is made [-p must be active!]', default=False)
opt.add_option('-r', '--rmsfile', help='RMSs file with one entry per line entry like "filename RMS_VALUE FLUX_ERR_%_VALUE"')
(options, imglist) = opt.parse_args()
outimg = options.outimg
maskimg = options.maskimg
maskplot = options.maskplot
doplot = options.doplot
dozone = options.dozone
rmsfile = options.rmsfile
print "Output file: "+outimg
sys.stdout.flush()

# Read RMS values from file
if (rmsfile != None):
	print "Reading RMS file: "+rmsfile
	try:
	    rmsdata = np.loadtxt(rmsfile, comments='#', dtype=np.dtype({'names':['file','rms','fluxerr'], 'formats':['S50',float,float]}))
	except IOError:
	    print "ERROR: error opening RMSs file, probably a wring name/format"
	    exit(1)

# Read images one by one.
values = []
frequencies = []
rmsvalues = []
fluxerrvalues = []
for name in imglist:
    print "--- Reading file: "+name
    try:
      image = pyrap.images.image(name)
      # workaround for getting correct axes
      index = np.where(np.array(image.coordinates().get_names())=='direction')[0][0]
      imgdata = np.array(image.getdata())
      for i in xrange(index):
        imgdata = imgdata[0]
      values.append(imgdata)
      frequencies.append(image.coordinates().get_referencevalue()[0])
    except:
      print "ERROR: error accessing iamges data, probably wrong name or data format"
      exit(1)
    print "Freq: ", image.coordinates().get_referencevalue()[0]
    if (rmsfile != None):
      try:
	rmsvalues.append([rms for (file, rms, fluxerr) in rmsdata  if file == name][0])
	fluxerrvalues.append([fluxerr for (file, rms, fluxerr) in rmsdata  if file == name][0])
        print "RMS: ", [rms for (file, rms, fluxerr) in rmsdata  if file == name][0]
        print "Flux err %: ", [fluxerr for (file, rms, fluxerr) in rmsdata  if file == name][0]
      except IndexError:
	print "ERROR: error accessing RMSs data, probably wrong names in the file"
	exit(1)
    else:
	rmsvalues.append(0)
	fluxerrvalues.append(0)
    sys.stdout.flush()

values = np.array(values)
frequencies = np.array(frequencies)
rmsvalues = np.array(rmsvalues)
imgsizeX = values.shape[1]
imgsizeY = values.shape[2]

# var used if dozone=T
zoneval4reg=np.zeros(len(imglist))
zonepixelnum=0

# Read mask image
if (maskimg):
  print "Reading mask-image = "+maskimg
  sys.stdout.flush()
  image = pyrap.images.image(maskimg)
  maskimgval = np.array(image.getdata()[0][0])
else:
  maskimgval = np.ones(imgsizeX*imgsizeY)
  maskimgval = maskimgval.reshape(imgsizeX, imgsizeY)

# Read mask plot
if (doplot):
  if (maskplot):
    print "Reading mask-plot = "+maskplot
    sys.stdout.flush()
    image = pyrap.images.image(maskplot)
    maskplotval = np.array(image.getdata()[0][0])
    if (dozone): print "      |- 1 zone averaging"
  elif (maskimg):
    # if we do not have mask plot set but we have mask img set, use that one
    print "WARNING: using mask-image for mask-plot"
    maskplotval = maskimgval
  else:
    print "WARNING: plotting every point! It could take a long time!"
    maskplotval = np.ones(imgsizeX*imgsizeY)
    maskplotval = maskplotval.reshape(imgsizeX, imgsizeY)

print ""
print "Performing regression",
sys.stdout.flush()

# Make regression pixel by pixel
spidx = np.empty(imgsizeX*imgsizeY)
err = np.empty(imgsizeX*imgsizeY)
spidx = spidx.reshape(imgsizeX, imgsizeY)
err = err.reshape(imgsizeX, imgsizeY)
val4reg = np.empty(len(imglist))
for i in range(0, imgsizeX):
  for j in range(0, imgsizeY):
    val4reg=values.transpose()[j][i]
    # if the mask is set to 0 or rms check is true then skip this pixel
    if (maskimgval[i][j] == 0 or (rmsfile != None and badrms(val4reg, rmsvalues))):
      a = np.nan # TODO: set a mask, not to 0
      sa = np.nan # TODO: set a mask, not to 0
    else:
      # err of log of quadrature sum of rms and fluxscale-error
      yerr=0.434*np.sqrt(rmsvalues**2+(fluxerrvalues*val4reg/100)**2)/val4reg
      (a, b, sa, sb) = linear_fit(x=np.log10(frequencies), y=np.log10(val4reg), yerr=yerr)
      if (doplot and maskplotval[i][j] == 1):
        if (dozone):
          zoneval4reg += val4reg
	  zonepixelnum += 1
	else:
#    	  print "!",
          sys.stdout.flush()
          fig = pylab.figure(figsize=(8, 8))
          ax = fig.add_subplot(111)
          yerr=0.434*np.sqrt(rmsvalues**2+(fluxerrvalues*val4reg/100)**2)/val4reg
          ax.errorbar(np.log10(frequencies), np.log10(val4reg), yerr=yerr, fmt='bo')
          xmin, xmax = ax.get_xlim()
          ax.plot([xmin, xmax], [a*xmin+b, a*xmax+b], 'r-')
  	  fig.savefig('plots/'+str(i)+'-'+str(j)+'.png')
  	  fig.clf()

    spidx[i][j] = a
    err[i][j] = -100*sa/a # error in %
  sys.stdout.write('\r')
  sys.stdout.write(" [%-20s] %d%%" % ('='*(i*20/imgsizeX), i*100/imgsizeX))
  sys.stdout.flush()

print "\n\n"
# write the zone data
if (dozone):
  print "Writing zone data."
  zoneval4reg = zoneval4reg / zonepixelnum
  (a, b, sa, sb) = linear_fit(xdata=np.log10(frequencies), ydata=np.log10(zoneval4reg), ysigma=0.434*(rmsvalues/zoneval4reg))
  sys.stdout.flush()
  fig = pylab.figure(figsize=(8, 8))
  ax = fig.add_subplot(111)
  ax.errorbar(np.log10(frequencies), np.log10(zoneval4reg), yerr=0.434*(rmsvalues/zoneval4reg), fmt='bo')
  xmin, xmax = ax.get_xlim()
  ax.plot([xmin, xmax], [a*xmin+b, a*xmax+b], 'r-')
  print "Spidx zone: "+str(a)+" +/- "+str(sa)
  fig.savefig('plots/zone.png')
  fig.clf()
  np.savetxt('plots/zone.txt', np.array([frequencies,zoneval4reg]).transpose())
  
# Write data
print "Writing spidx data."
spidximg = pyrap.images.image(imglist[-1])
spidximg.saveas(outimg)
spidximg = pyrap.images.image(outimg)
spidximg.putdata(spidx)
#spidximg.tofits(outimg + ".fits")
del spidximg
rmsimg = pyrap.images.image(imglist[-1])
rmsimg.saveas(outimg + "-rms")
rmsimg = pyrap.images.image(outimg + "-rms")
rmsimg.putdata(err)
#rmsimg.tofits(outimg + "-rms.fits")
del rmsimg

print "Done."
sys.stdout.flush()
