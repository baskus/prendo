# coding=utf-8

# Copyright (c) 2013 Sebastian Ärleryd
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from google.appengine.api import memcache
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.runtime import DeadlineExceededError
import logging

import config
from country import Country
from score import Score

class CronJob(webapp.RequestHandler):
	def clean_country( self, control, location, lowest_score ):
		scores = Score.all() \
			.filter( "control =", control ) \
			.filter( "location =", location ) \
			.filter( "points <", lowest_score ) \
			.filter( "new_week =", False ) \
			.fetch( 400 )
		
		high = 0
		for s in scores:
			if s.points > high:
				high = s.points
		
		try:
			db.delete( scores )
		except Exception, msg:
			logging.error( "Got exception: '%s'. Some or all deletes might " \
				+ "have failed.", msg )
	
	def get(self):
		self.response.out.write( "cronjob here!<br /><br />" )
		
		clean_invisible = unicode( self.request.get( "clean_invisible" ) )
		if clean_invisible == "yes":
			# Get a location.
			location = Country.get_random_location()
			# Get the locations lowest high score.
			for control in config.VALID_CONTROLS:
				lowest_score = Score.get_lowest_score( control, location )
				if lowest_score is None:
					continue
				self.clean_country( control, location, lowest_score )
		
		flush = unicode( self.request.get( "flush" ) )
		if flush == "yes":
			memcache.flush_all()
		
		reflag_week_shallow = unicode( self.request.get(
			"reflag_week_shallow" ) )
		if reflag_week_shallow == "yes":
			Score.reflag_new_week()
			
			Score._delete_cached_list( "tilt", config.LOCATION_WEEK )
			Score._delete_cached_list( "touch", config.LOCATION_WEEK )
			
			for control in ("tilt", "touch"):
				Score.get_top_list(config.TOP_LIST_LENGTH, control,
					config.LOCATION_WEEK)
		
		clear_world_week_duplicates = unicode( self.request.get(
			"clear_world_week_duplicates" ) )
		if clear_world_week_duplicates == "yes":
			for location in ( config.LOCATION_WORLD, config.LOCATION_WEEK ):
				for control in config.VALID_CONTROLS:
					self.delete_duplicates( control, location )
		
		clear_random_country_duplicates = unicode( self.request.get(
			"clear_random_country_duplicates" ) )
		if clear_random_country_duplicates == "yes":
			location = Country.get_random_location()
			for control in config.VALID_CONTROLS:
				self.delete_duplicates( control, location )
		
		clear_country_duplicates = unicode( self.request.get(
			"clear_country_duplicates" ) )
		if clear_country_duplicates != "":
			for control in config.VALID_CONTROLS:
				self.delete_duplicates( control, clear_country_duplicates )
		
		clear_all_country_duplicates = unicode( self.request.get(
			"clear_all_country_duplicates" ) )
		if clear_all_country_duplicates == "yes":
			start_location = Country.next_country()
			location = start_location
			count = 0
			try:
				while True:
					for control in config.VALID_CONTROLS:
						self.delete_duplicates( control, location )
					location = Country.next_country()
					count += 1
			except DeadlineExceededError, ex:
				logging.error( "CronJob.get: Got DeadlineExceededError. " \
					+ "Managed to clear %d countries from \"%s\" to \"%s\"",
					count, start_location, location )
				return
	
	def delete_duplicates( self, control, location ):
		scores = Score.all().filter( "control =", control )
		
		if not location in ( config.LOCATION_WORLD, config.LOCATION_WEEK ):
			scores = scores.filter( "location =", location )
		
		elif location == config.LOCATION_WEEK:
			scores = scores.filter( "new_week =", True )
		elif location != config.LOCATION_WORLD:
			logging.error( "CronJob.delete_duplicates: Got an invalid " \
				+ "location \"%s\".", location )
			return
		
		fetched = scores.order( "-points" ).fetch( config.TOP_LIST_LENGTH )
		fetched = sorted( fetched, key=lambda score: score.date )
		
		to_remove = []
		i = 0
		while i < len( fetched ):
			scorei = fetched[i]
			j = i+1
			while j < len( fetched ):
				scorej = fetched[j]
				if not scorei is scorej and scorei.equals( scorej ) \
						and not scorej in to_remove:
					to_remove.append( scorej )
				j += 1
			i += 1
		
		have = [ score.to_dict() for score in fetched ]
		logging.info( "Location: \"%s\" Have %d scores: %s", location,
			len( have ), have )
		
		would = [ score.to_dict() for score in to_remove ]
		logging.info( "Location: \"%s\"Would remove %d scores: %s", location,
			len( would ), would )
		
		count1 = 0
		for score in to_remove:
			if score in fetched:
				count1 += 1
		
		count2 = 0
		for score in fetched:
			if score in to_remove:
				count2 += 1
		
		logging.info( "count1: %d, count2: %d", count1, count2 )
		
		try:
			db.delete( to_remove )
			self.response.out.write(
				"<br />all entities deleted successfully." )
		except Exception, msg:
			self.response.out.write( "<br />Got exception: '%s'." % msg \
				"<br />Some or all deletes might have failed." )
		
		if len(to_remove) > 0:
			# Clear the old lists.
			Score._delete_cached_list( control, location )
			# Request new lists so that they're cached.
			Score.get_top_list( config.TOP_LIST_LENGTH, control, location )

application = webapp.WSGIApplication( [ ( "/cronjob", CronJob ) ] )

def main():
	run_wsgi_app( application )

if __name__ == "__main__":
	main()
