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

from google.appengine.ext.webapp.util import run_wsgi_app
import logging
#import json
from django.utils import simplejson as json
#import webapp2
from google.appengine.ext import webapp

import config
from score import Score

class RequestAndSubmitHandler( webapp.RequestHandler ):
	def send_response( self, success, request_response=None ):
		response = {
			"request": request_response,
		}
		
		raw_json = json.dumps( response )
		
		self.response.out.write( raw_json )
	
	def handle_request( self, request, location ):
		"""Return a json object with keys control and data.
		
		Example return value:
		{
			"control": "touch",
			"data": (local_list, world_list, week_list)
		}
		
		where the data *_list entries are string dumps of json objects
		containing information about a top list as returned by
		Score.get_top_list.
		
		"""
		
		# Can't do anything if we didn't get any request data.
		if request is None:
			logging.error( "RequestAndSubmitHandler.handle_request: got None " \
				+ "request" )
			return
		
		control = None
		try:
			control = request["control"]
		except Exception, e:
			logging.error( "handle_request: failed to get control. " \
				+ "Exception: %s", repr( e ) )
			return
		
		if not control in config.VALID_CONTROLS:
			logging.error( "handle_request: got invalid control %s.", control )
			return
		
		# Get the json dump part of the top lists.
		local_json = Score.get_top_list( config.TOP_LIST_LENGTH, control,
			location )[0]
		world_json = Score.get_top_list( config.TOP_LIST_LENGTH, control,
			config.LOCATION_WORLD )[0]
		week_json = Score.get_top_list( config.TOP_LIST_LENGTH, control,
			config.LOCATION_WEEK )[0]
		
		to_return = {
			"control": control,		# "tilt" / "touch"
			"data": ( local_json, world_json, week_json ),
		}
		
		return to_return
	
	def handle_submit( self, submit_data, location ):
		"""Submit a json encoded list of scores"""
		
		if submit_data is None:
			logging.error( "RequestAndSubmitHandler.handle_submit: got None " \
				+ "submit_data" )
			return False
		
		try:
			code = submit_data["code"]
			scores = submit_data["scores"]
		except Exception, ex:
			logging.error( "Got exception when getting common data: %s. " \
				+ "Common data was: %s", repr( ex ), str( submit_data ) )
			return False
		
		# Check the code first to see if the submit should be accepted.
		if code != config.SECRET_SUBMIT_CODE:
			logging.error( "handle_submit: invalid code. Code was: \"%s\"",
				code )
			return False
		
		if len( scores ) == 0:
			return True
		
		# As the list of scores are submitted, their respective points are
		# checkd whether they would show up on a list. If they would not, and
		# since the list is sorted descending, we know any subsequent score
		# with the same control type wont show up either.
		# Therefore we want to remember if a score of a specific control would
		# not show up. If it would not, subsequent scores with the same control
		# type will be skipped and not submitted. For this purpose, create
		# dictionary { "touch": True, "tilt": True } for config.VALID_CONTROLS =
		# ( "touch", "tilt" ). Control types to initially submit are marked
		# when sorting.
		stop_submit = dict( ( cont, True ) for cont in config.VALID_CONTROLS )
		
		def sort_func(score):
			# Mark this control as used.
			c = score["control"]
			stop_submit[c] = False
			# Do the actual sorting.
			return -score["points"]
		
		# Sort descending by points.
		scores = sorted( scores, key=sort_func )
		
		submit_count = 0
		
		for score in scores:
			try:
				score_control = score["control"]
			except KeyError, ex:
				logging.warning( "handle_submit: Got KeyError when getting " \
					+ "score control." )
				continue
			
			if stop_submit[score_control]:
				logging.info( "handle_submit: Stop submit True for %s, " \
					+ "continuing.", score_control)
				continue
			
			try:
				score_points = score["points"]
			except KeyError, ex:
				logging.warning( "handle_submit: Got KeyError when getting " \
					+ "score points." )
				continue
			
			if not Score._would_show_on_location_or_week_lists( location,
					score_points, score_control ):
				# Since the scores list is sorted descending by points, no
				# score of this control will be postable from now on.
				stop_submit[score_control] = True
				logging.info( "handle_submit: Setting stop submit for %s, " \
					+ "continuing.", score_control)
				# If there are no control types left to check, stop the loop.
				if not ( False in stop_submit.values() ):
					logging.info( "handle_submit: Found no False in " \
						+ "stop_submit, breaking." )
					break
				continue
			
			try:
				name = score["name"]
				comment = score["comment"]
			except KeyError, ex:
				logging.warning( "handle_submit: Got KeyError: %s. " \
					+ "Dictionary: %s", repr( ex ), str( score ) )
				continue
			
			status = Score.submit( name, comment, score_points, score_control,
				location )
			
			submit_count += 1
			
			# If the submit fails because of an error, log as much of it as
			# possible so we maybe can submit it manually later.
			if status == Score.SUBMIT_FAIL:
				json_dump = "<could not dump json>"
				try:
					json_dump = json.dumps( score )
				except:
					pass
				logging.critical( "RequestAndSubmitHandler.handle_submit: " \
					+ "score submit failed. JSON: %s", json_dump)
				logging.info( "Submitted %d of %d scores before the error.",
					submit_count - 1, len( scores ) )
				
				return False
		
		logging.info( "Out of %d scores, submitted %d.", len( scores ),
			submit_count )
		
		return True
	
	def get( self ):
		self.post()
	
	def post( self ):
		
		# contents of the data:
		# {
		#	"request":
		#	{
		#		"control": "tilt" / "touch"
		#	}
		#	"submit:
		#	{
		#		"code": <submit code>
		#		"android_id": <android id>
		#		"scores":
		#		[
		#			{
		#				"name": <string value, max chars set in config>,
		#				"control": "tilt" / "touch",
		#				"points": <int points>,
		#				"comment": <string value, max chars set in config>,
		#			},
		#			{..., ...},
		#			...
		#		]
		#	}
		# }
		
		#
		# Extract the data from the data POST variable
		#
		data = self.request.get( "data" )
		json_data = None
		try:
			json_data = json.loads( data )
		except Exception, ex:
			logging.error( "RequestAndSubmitHandler.post: failed to extract " \
				+ "json data. Data: \"%s\". Exception: %s", data, repr( ex ) )
			self.error( 400 )		#Send a 400 Bad Request
			return
		
		#
		# Extract the request part of the data POST variable
		#
		request = None
		try:
			request = json_data["request"]
		except KeyError, ex:
			logging.error( "RequestAndSubmitHandler.post: Failed to extract " \
				+ "request from json data. Data: \"%s\". Exception: %s",
				data, repr( ex ) )
			self.error( 400 )		#Send a 400 Bad Request
			return
		
		#
		# Extract the submit part of the data POST variable
		#
		submit = None
		try:
			submit = json_data["submit"]
		except KeyError, ex:
			logging.error( "RequestAndSubmitHandler.post: Failed to extract " \
				+ "submit from json data. Data: \"%s\". Exception: %s",
				data, repr( ex ) )
			self.error( 400 )		#Send a 400 Bad Request
			return
		
		#
		# Check if both the request and submit was empty. If so then we can't
		# do anything but return fail.
		#
		if request is None and submit is None:
			logging.error( "RequestAndSubmitHandler.post: Got empty request " \
				+ "and submit" )
			self.error( 400 )		#Send a 400 Bad Request
			return
		
		location = self.request.headers["X-AppEngine-country"].lower()
		success = self.handle_submit(submit, location)
		request_response = self.handle_request(request, location)
		self.send_response(success, request_response)

application = webapp.WSGIApplication( [
	( "/ras", RequestAndSubmitHandler ) ] )

if __name__ == "__main__":
	run_wsgi_app( application )
