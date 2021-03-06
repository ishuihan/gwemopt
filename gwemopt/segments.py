
import os, sys
import numpy as np
import healpy as hp
import copy

import astropy.coordinates
from astropy.time import Time, TimeDelta
import astropy.units as u

import ephem

import glue.segments

import gwemopt.utils
import gwemopt.rankedTilesGenerator
import gwemopt.moc, gwemopt.pem

def get_skybrightness(config_struct,segmentlist,observer,fxdbdy,radec):

    moonsegmentlist = glue.segments.segmentlist()
    if config_struct["filter"] == "c":
        passband = "g"
    else:
        passband = config_struct["filter"]

    # Moon phase data (from Coughlin, Stubbs, and Claver Table 2) 
    moon_phases = [2,10,45,90]
    moon_data = {'u':[2.7,3.1,4.2,5.7],
                 'g':[2.4,2.8,3.8,5.2],
                 'r':[2.1,2.5,3.4,4.9],
                 'i':[1.9,2.3,3.3,4.7],
                 'z':[1.9,2.2,3.2,4.6],
                 'y':[1.8,2.2,3.1,4.5]}

    # Determine moon data for this phase
    moon_data_passband = moon_data[passband]

    # Fits to solar sky brightness (from Coughlin, Stubbs, and Claver Table 4) 
    sun_data = {'u':[88.5,-0.5,-0.5,0.4],
                'g':[386.5,-2.2,-2.4,0.8],
                'r':[189.0,-1.4,-1.1,0.8],
                'i':[164.8,-1.5,-0.7,0.6],
                'z':[231.2,-2.8,-0.7,1.4],
                'zs':[131.1,-1.4,-0.5,0.2],
                'y':[92.0,-1.3,-0.2,0.9]}

    sun_data_error = {'u':[6.2,0.1,0.1,0.1],
                'g':[34.0,0.2,0.2,0.5],
                'r':[32.7,0.2,0.2,0.5],
                'i':[33.1,0.2,0.2,0.5],
                'z':[62.3,0.3,0.4,0.9],
                'zs':[45.6,0.2,0.3,0.6],
                'y':[32.7,0.2,0.2,0.5]}

    # Determine sun data for this phase
    sun_data_passband = sun_data[passband]

    dt = 1.0/24.0
    tt = np.arange(segmentlist[0][0],segmentlist[-1][1]+dt,dt)

    ra2 = radec.ra.radian
    d2 = radec.dec.radian

    # Where is the moon?
    moon = ephem.Moon()
    for ii in xrange(len(tt)-1):
        observer.date = ephem.Date(Time(tt[ii], format='mjd', scale='utc').iso)
        moon.compute(observer)
        fxdbdy.compute(observer)

        alt_target = float(repr(fxdbdy.alt)) * (360/(2*np.pi))
        az_target = float(repr(fxdbdy.az)) * (360/(2*np.pi))
        #print "Altitude / Azimuth of target: %.5f / %.5f"%(alt_target,az_target)

        alt_moon = float(repr(moon.alt)) * (360/(2*np.pi))
        az_moon = float(repr(moon.az)) * (360/(2*np.pi))
        #print "Altitude / Azimuth of moon: %.5f / %.5f"%(alt_moon,az_moon)

        if (alt_target < 30.0) or (alt_moon < 30.0):
            total_mag, total_mag_error, flux_mag, flux_mag_error = np.inf, np.inf, np.inf, np.inf
        else:
            ra_moon = (180/np.pi)*float(repr(moon.ra))
            dec_moon = (180/np.pi)*float(repr(moon.dec))

            # Coverting both target and moon ra and dec to radians
            ra1 = float(repr(moon.ra))
            d1 = float(repr(moon.dec))

            # Calculate angle between target and moon
            cosA = np.sin(d1)*np.sin(d2) + np.cos(d1)*np.cos(d2)*np.cos(ra1-ra2)
            angle = np.arccos(cosA)*(360/(2*np.pi))
            #print "Angle between moon and target: %.5f"%(angle)

            delta_mag = np.interp(moon.moon_phase*100.0,moon_phases,moon_data_passband)
            delta_mag_error = 0.1*delta_mag

            flux = sun_data_passband[0] + sun_data_passband[1]*angle +\
                sun_data_passband[2]*alt_target + sun_data_passband[3]*alt_moon
            flux_zp = sun_data_passband[0] + sun_data_passband[1]*90.0 +\
                sun_data_passband[2]*90.0 + sun_data_passband[3]*45.0

            # check if flux < 0: too small to fit
            if flux < 0:
                flux = 1e-10

            flux = flux* (10**11)
            flux_zp = flux_zp* (10**11)
            flux_mag = -2.5 * (np.log10(flux) - np.log10(flux_zp))

            sun_data_passband_error = sun_data_error[passband]
            flux_error = np.sqrt(sun_data_passband_error[0]**2 + sun_data_passband_error[1]**2 * angle**2 +\
                sun_data_passband_error[2]**2 * alt_target**2 + sun_data_passband_error[3]**2 * alt_moon**2)
            flux_error = flux_error * (10**11)

            flux_mag_error = 1.08574 * flux_error / flux

            # Determine total magnitude contribution
            total_mag = delta_mag + flux_mag
            total_mag_error = np.sqrt(delta_mag_error**2 + flux_mag_error**2)
            #print tt[ii], angle, alt_target, alt_moon, total_mag, total_mag_error
        if total_mag > 0.0:
            segment = glue.segments.segment(tt[ii],tt[ii+1])
            moonsegmentlist = moonsegmentlist + glue.segments.segmentlist([segment])
            moonsegmentlist.coalesce()
        #else:
        #    print tt[ii], angle, alt_target, alt_moon, total_mag, total_mag_error

    moonsegmentlistdic = glue.segments.segmentlistdict()
    moonsegmentlistdic["observations"] = segmentlist
    moonsegmentlistdic["moon"] = moonsegmentlist
    moonsegmentlist = moonsegmentlistdic.intersection(["observations","moon"])
    moonsegmentlist.coalesce()

    #print "Keeping %.2f %% of data"%(100.0*np.sum(np.diff(moonsegmentlist))/np.sum(np.diff(segmentlist)))

    return moonsegmentlist

def get_segments(params, config_struct):

    gpstime = params["gpstime"]
    event_mjd = Time(gpstime, format='gps', scale='utc').mjd

    segmentlist = glue.segments.segmentlist()
    n_windows = len(params["Tobs"]) // 2
    start_segments = event_mjd + params["Tobs"][::2]
    end_segments = event_mjd + params["Tobs"][1::2]
    for start_segment, end_segment in zip(start_segments,end_segments):
        segmentlist.append(glue.segments.segment(start_segment,end_segment))

    observer = ephem.Observer()
    observer.lat = str(config_struct["latitude"])
    observer.lon = str(config_struct["longitude"])
    observer.horizon = str(-12.0)
    observer.elevation = config_struct["elevation"]

    date_start = ephem.Date(Time(segmentlist[0][0], format='mjd', scale='utc').iso)
    date_end = ephem.Date(Time(segmentlist[-1][1], format='mjd', scale='utc').iso)
    observer.date = ephem.Date(Time(segmentlist[0][0], format='mjd', scale='utc').iso)

    sun = ephem.Sun()
    nightsegmentlist = glue.segments.segmentlist()
    while date_start < date_end:
        date_rise = observer.next_rising(sun, start = date_start)
        date_set = observer.next_setting(sun, start = date_start)
        if date_set > date_rise:
            date_set = observer.previous_setting(sun, start = date_start)

        astropy_rise = Time(date_rise.datetime(), scale='utc').mjd
        astropy_set  = Time(date_set.datetime(), scale='utc').mjd

        segment = glue.segments.segment(astropy_set,astropy_rise)
        nightsegmentlist = nightsegmentlist + glue.segments.segmentlist([segment])
        nightsegmentlist.coalesce()

        date_start = date_rise
        observer.date = date_rise

    segmentlistdic = glue.segments.segmentlistdict()
    segmentlistdic["observations"] = segmentlist
    segmentlistdic["night"] = nightsegmentlist
    segmentlist = segmentlistdic.intersection(["observations","night"])
    segmentlist.coalesce()

    return segmentlist

def get_segments_tile(config_struct, observatory, radec, segmentlist):

    observer = ephem.Observer()
    observer.lat = str(config_struct["latitude"])
    observer.lon = str(config_struct["longitude"])
    observer.horizon = str(30.0)
    observer.elevation = config_struct["elevation"]

    fxdbdy = ephem.FixedBody()
    fxdbdy._ra = ephem.degrees(str(radec.ra.degree))
    fxdbdy._dec = ephem.degrees(str(radec.dec.degree))

    observer.date = ephem.Date(Time(segmentlist[0][0], format='mjd', scale='utc').iso)
    fxdbdy.compute(observer)

    date_start = ephem.Date(Time(segmentlist[0][0], format='mjd', scale='utc').iso)
    date_end = ephem.Date(Time(segmentlist[-1][1], format='mjd', scale='utc').iso)
    tilesegmentlist = glue.segments.segmentlist()
    while date_start < date_end:
        try:
            date_rise = observer.next_rising(fxdbdy, start=observer.date)
            date_set = observer.next_setting(fxdbdy, start=observer.date)
            if date_rise > date_set:
                date_rise = observer.previous_rising(fxdbdy, start=observer.date)
        except ephem.AlwaysUpError:
            date_rise = date_start
            date_set = date_end
        except ephem.NeverUpError:
            date_rise = ephem.Date(0.0)
            date_set = ephem.Date(0.0)
            break

        astropy_rise = Time(date_rise.datetime(), scale='utc')
        astropy_set  = Time(date_set.datetime(), scale='utc')

        astropy_rise_mjd = astropy_rise.mjd
        astropy_set_mjd  = astropy_set.mjd
        # Alt/az reference frame at observatory, now
        #frame_rise = astropy.coordinates.AltAz(obstime=astropy_rise, location=observatory)
        #frame_set = astropy.coordinates.AltAz(obstime=astropy_set, location=observatory)    
        # Transform grid to alt/az coordinates at observatory, now
        #altaz_rise = radec.transform_to(frame_rise)
        #altaz_set = radec.transform_to(frame_set)        

        segment = glue.segments.segment(astropy_rise_mjd,astropy_set_mjd)
        tilesegmentlist = tilesegmentlist + glue.segments.segmentlist([segment])
        tilesegmentlist.coalesce()

        date_start = date_set
        observer.date = date_set

    moonsegmentlist = get_skybrightness(\
        config_struct,segmentlist,observer,fxdbdy,radec)

    tilesegmentlistdic = glue.segments.segmentlistdict()
    tilesegmentlistdic["observations"] = segmentlist
    tilesegmentlistdic["tile"] = tilesegmentlist
    tilesegmentlistdic["moon"] = moonsegmentlist
    tilesegmentlist = tilesegmentlistdic.intersection(["observations","tile","moon"])
    tilesegmentlist.coalesce()

    return tilesegmentlist

def get_segments_tiles(config_struct, tile_struct, observatory, segmentlist):

    print "Generating segments for tiles..."

    ras = []
    decs = []
    keys = tile_struct.keys()
    for key in keys:
        ras.append(tile_struct[key]["ra"])
        decs.append(tile_struct[key]["dec"])

    # Convert to RA, Dec.
    radecs = astropy.coordinates.SkyCoord(
            ra=np.array(ras)*u.degree, dec=np.array(decs)*u.degree, frame='icrs')
    tilesegmentlists = []
    for ii,radec in enumerate(radecs):
        #if np.mod(ii,100) == 0: 
        #    print "Generating segments for tile %d/%d"%(ii+1,len(radecs))
        tilesegmentlist = get_segments_tile(config_struct, observatory, radec, segmentlist)
        tilesegmentlists.append(tilesegmentlist)

    return tilesegmentlists
