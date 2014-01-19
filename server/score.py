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

import datetime
from google.appengine.api import memcache
from google.appengine.ext import db
from google.appengine.ext import ndb
import json
import logging
import time

import config
from country import Country

# Singleton scorelist entity type
class Scorelist(db.Model):
	pass

# Use the single Scorelist instance as a common parent for all Score instances
# to be able to use ancestor queries and thus avoid problems
# with the High Replication data store
def scorelist_key():
	single_scorelist = Scorelist(key_name="all_scores")
	single_scorelist.put()
	return single_scorelist.key()

class Score( db.Model ):
	SUBMIT_FAIL = 0
	SUBMIT_SUCCESS = 1
	# Like success, except the score already existed. Safe. :)
	SUBMIT_SKIPPED = 2
	
	name = db.StringProperty( required=True, multiline=False )
	comment = db.StringProperty( multiline=False, default="" )
	points = db.IntegerProperty( required=True )
	control = db.StringProperty( required=True, multiline=False )
	location = db.StringProperty( required=True, multiline=False )
	date = db.DateTimeProperty( auto_now_add=True )
	new_week = db.BooleanProperty( required=True, default=True )
	
	def equals( self, other ):
		return self.name == other.name \
			and self.comment == other.comment \
			and self.points == other.points \
			and self.control == other.control \
			and self.location == other.location
	
	def to_dict(self):
		# Convert the date to unix time.
		tt = self.date.timetuple()
		ut = time.mktime( tt )
		
		d = {
			"name": self.name,
			"comment": self.comment,
			"points": self.points,
			"control": self.control,
			"location": self.location,
			"date": ut,
		}
		
		return d
	
	@classmethod
	def submit( cls, name, comment, points, control, location ):
		# Check that the control is valid.
		if not control in config.VALID_CONTROLS:
			logging.error( "Score.submit: invalid control \"%s\"", control )
			return Score.SUBMIT_FAIL
		
		# Check that we got a name.
		if name == "":
			logging.error("Score.submit: got empty name")
			return Score.SUBMIT_FAIL
		
		# Check that we got points.
		if points == "":
			logging.error( "Score.submit: got empty points" )
			return Score.SUBMIT_FAIL
		# Catch the cases where points is not a number.
		try:
			points = int( points )
		except ValueError:
			logging.error( "Score.submit: points not an int" )
			return Score.SUBMIT_FAIL
		# Check that points >= 0.
		if points < 0:
			logging.error( "Score.submit: points has to be >= 0 but was %d",
				points )
			return Score.SUBMIT_FAIL
		
		# Check the length of the name.
		if len(name) > config.SCORE_NAME_MAX_LENGTH:
			logging.warning( "Score.submit: Got name longer than the maximum " \
				+ "allowed %d characters. Truncating.",
				config.SCORE_NAME_MAX_LENGTH )
			name = name[:config.SCORE_NAME_MAX_LENGTH]
		
		# Check the length of the comment.
		if len(comment) > config.SCORE_COMMENT_MAX_LENGTH:
			logging.warning( "Score.submit: Got comment longer than the " \
				+ "maximum allowed %d characters. Truncating.",
				config.SCORE_COMMENT_MAX_LENGTH )
			comment = comment[:config.SCORE_COMMENT_MAX_LENGTH]
		
		# Check the location.
		if location == "":
			logging.error( "Score.submit: Got invalid location \"\"" )
			return Score.SUBMIT_FAIL
		
		if not cls._would_show_on_location_or_week_lists( location, points,
				control ):
			logging.info( "Score.submit: Score wouldn't show up on neither " \
				+ "it's location list (%s) nor the week list, skip saving. " \
				+ "(%s, %s, %d)", location, name, comment, points )
			return Score.SUBMIT_SKIPPED
		else:
			logging.info( "Score.submit: Score would show up, continuing. " \
				+ "(%s, %s, %d)", name, comment, points )
		
		# Check if there is an identical score, from any time. If there is, then
		# SUCCEDE silently.
		if cls._already_exists( name, comment, points, control ):
			logging.info("Score.submit: Score already exists, skip saving. "
				+ "(%s, %s, %d)", name, comment, points )
			return Score.SUBMIT_SKIPPED
		
		try:
			new_score = Score( name=name,
				comment=comment,
				points=points,
				control=control,
				location=location,
				parent=scorelist_key() )
		except Exception, e:
			logging.error( "Score.submit: Got exception when creating Score " \
				+ "model object. Type: %s, msg: %s", type( e ), e )
			return Score.SUBMIT_FAIL
		
		try:	
			new_score.put()
		except Exception, e:
			logging.error( "Score.submit: Got exception when putting score " \
				+ "to the datastore. Type: %s, msg: %s", type( e ), e )
			return Score.SUBMIT_FAIL
		
		# Save the location of the submit.
		try:
			Country.save( location )
		except Exception, msg:
			logging.warning( "Score.submit: Got exception when saving " \
				+ "location: '%s'", msg )
		
		cls._delete_cached_list_if_invalid( control, location, points )
		cls._delete_cached_list_if_invalid( control, config.LOCATION_WORLD,
			points )
		cls._delete_cached_list_if_invalid( control, config.LOCATION_WEEK,
			points )
		
		return Score.SUBMIT_SUCCESS
	
	@classmethod
	def _already_exists( cls, name, comment, points, control ):
		scores = Score.all().ancestor(scorelist_key()) \
			.filter( "name =", name ) \
			.filter( "comment =", comment ) \
			.filter( "points =", points ) \
			.filter( "control =", control )
		fetched = scores.fetch( 100 )
		
		return len( fetched ) > 0
	
	@classmethod
	def _would_show_on_location_or_week_lists( cls, location, points, control ):
		"""Return whether or not a score with this number of points would show
		up on it's location list or the week list."""
		
		location_list = cls.get_top_list( config.TOP_LIST_LENGTH, control,
			location )
		week_list = cls.get_top_list( config.TOP_LIST_LENGTH, control,
			config.LOCATION_WEEK )
		
		if location_list[1] < config.TOP_LIST_LENGTH \
				or week_list[1] < config.TOP_LIST_LENGTH:
			return True
		
		location_low_score = location_list[2]
		week_low_score = week_list[2]
		lowest_low_score = min( location_low_score, week_low_score )
		
		logging.info( "Score._would_show_on_location_or_week_lists: " \
			+ "location_low_score=%d, week_low_score=%d, lowest_low_score=%d",
			location_low_score, week_low_score, lowest_low_score )
		
		return points >= lowest_low_score
	
	@classmethod
	def deep_reflag_new_week( cls ):
		"""Reflag all scores (maximum 1000)."""
		
		# Flag all new true.
		scores = Score.all().ancestor(scorelist_key())
		scores = scores.order( "-date" )
		
		time_delta = datetime.timedelta( seconds=config.WEEK_LIST_TIME )
		now = datetime.datetime.now()
		start_of_period = now - time_delta
		
		scores = scores.filter( "date >", start_of_period )
		fetched = scores.fetch( 1000 )
		
		for f in fetched:
			f.new_week = True
		
		db.put( fetched )
		
		# Flag all old false.
		scores = Score.all().ancestor(scorelist_key())
		
		scores = scores.order( "-date" )
		
		time_delta = datetime.timedelta( seconds=config.WEEK_LIST_TIME )
		now = datetime.datetime.now()
		start_of_period = now - time_delta
		
		scores = scores.filter( "date <", start_of_period )
		
		fetched = scores.fetch( 1000 )
		
		for f in fetched:
			f.new_week = False
		
		db.put( fetched )
	
	@classmethod
	def reflag_new_week( cls ):
		"""Set scores with new_week = True to new_week = False if they are older
		than one week"""
		
		scores = Score.all().ancestor(scorelist_key()).filter( "new_week =", True )
		
		scores = scores.order( "-date" )
		
		time_delta = datetime.timedelta( seconds=config.WEEK_LIST_TIME )
		now = datetime.datetime.now()
		start_of_period = now - time_delta
		
		scores = scores.filter( "date <", start_of_period )
		fetched = scores.fetch( 1000 )
		
		# Make sure we don't try to put more than 500 at a time since
		# that will cause a crash (gae won't allow it).
		while len( fetched ) > 100:
			to_put, fetched = fetched[:100], fetched[100:]
			
			for f in to_put:
				f.new_week = False
			db.put( to_put )
		
		# Put the last bit.
		if len( fetched ) > 0:
			for f in fetched:
				f.new_week = False
			db.put( fetched )
	
	@classmethod
	def _get_top_raw( cls, count, control, location ):
		"""Fetch the top #count scores for the control and location directly
		from the store.
		
		Retuns a possibly empty list of Model entities.
		
		"""
		
		if not isinstance( count, int ) or count <= 0:
			raise ValueError( "count has to be an integer > 0" )
		
		if not control in config.VALID_CONTROLS:
			raise ValueError( "Invalid control \"%s\"" % control )
		
		scores = Score.all().ancestor(scorelist_key()).filter( "control =", control )		

		if not location in ( config.LOCATION_WORLD, config.LOCATION_WEEK ):
			scores = scores.filter( "location =", location )
		
		if location == config.LOCATION_WEEK:
			scores = scores.filter( "new_week =", True )
		
		scores = scores.order( "-points" )
		fetched = scores.fetch( count )
		
		return fetched
	
	@classmethod
	def _cache_list( cls, control, location, list_json, length,
			lowest_score_points ):
		"""Cache a list as a tuple of (cached list json, list length, lowest
		score points)."""
		list_key = "list:%s:%s" % ( control, location )
		value = ( list_json, length, lowest_score_points )
		memcache.set( list_key, value )
	
	@classmethod
	def _get_cached_list( cls, control, location ):
		"""Return a tuple of (cached list json, list length, lowest score
		points)."""
		list_key = "list:%s:%s" % ( control, location )
		cached_value = memcache.get( list_key )
		return cached_value
	
	@classmethod
	def _delete_cached_list_if_invalid( cls, control, location, points ):
		list_key = "list:%s:%s" % ( control, location )
		cached_value = memcache.get( list_key )
		if cached_value is not None:
			cached_json, length, lowest_score_points = cached_value
		else:
			return
		
		if length < config.TOP_LIST_LENGTH or points >= lowest_score_points:
			m = memcache.delete( list_key )
	
	@classmethod
	def _delete_cached_list( cls, control, location ):
		list_key = "list:%s:%s" % ( control, location )
		result = memcache.delete( list_key )
		if result == 0:
			logging.error( "Score._delete_cached_list: Failed to delete " \
				+ "memcache key \"%s\", got network error!", list_key )
		elif result == 1:
			logging.info( "Score._delete_cached_list: Memcache key \"%s\" " \
				+ "doesn't exist, nothing deleted.", list_key )
		elif result == 2:
			logging.info( "Score._delete_cached_list: Memcache key \"%s\" " \
				+ "successfully deleted.", list_key )
	
	@classmethod
	def get_top_list( cls, count, control, location ):
		"""Return a tuple (json_dump, length, lowest_points) where json_dump is
		a dump of a json object containing information about a top list, length
		is the number of scores in the list and lowest_points is the number of
		points of the worst score in the list.
		
		Parameters:
		count - The number of entries in the list. Only used when building a new
			list, ignored when returning a cached list.
		control - The control type of the list to retrieve.
		location - The location of a list to retrieve. Can be a country code,
			config.LOCATION_WEEK or config.LOCATION_WORLD.
		
		json_dump example:
		{
			"location": <country_code>,
			"scores": [score_dict1, score_dict2, ...]
		}
		
		where score_dict* is a json object of the form:
		{
			"name": <name>
			"comment": <comment>
			"points": <points>
			"control": <control>
			"location": <location>
			"date": <unix time>
		}
		
		"""
		
		# Check for a cached list and return it if we get one.
		cached_list = cls._get_cached_list( control, location )
		if not cached_list is None:
			logging.info( "get_top_list: Returning cached list." )
			return cached_list
		
		# Get a raw list of scores from the datastore.
		raw_list = cls._get_top_raw( count, control, location )
		logging.info( "get_top_list: Raw list length is %d" % len( raw_list ) )
		# Get the last placed score.
		last_place = None
		try:
			last_place = raw_list[-1]
		except IndexError:
			pass
		
		dict_scores = []
		for score in raw_list:
			dict_scores.append( score.to_dict() )
		
		prepped_for_json = {
			"location": location,
			"scores": dict_scores,
		}
		dumped_json = json.dumps( prepped_for_json )
		
		# Figure out the last place points.
		if last_place is None:
			last_place_points = 0
		else:
			last_place_points = last_place.points
		
		#save the json list and the last placed points to the memcache
		cls._cache_list( control, location, dumped_json, len( raw_list ),
			last_place_points )
		
		return ( dumped_json, len( raw_list ), last_place_points )
	
	@classmethod
	def get_lowest_score( cls, control, location ):
		"""Get the lowest score for control and location from memcache."""
		
		cached_list = Score._get_cached_list( control, location )
		if cached_list is None:
			return None
		else:
			lowest_score = cached_list[2]
			try:
				lowest_scrore = int( lowest_score )
			except:
				return None
			return lowest_score
