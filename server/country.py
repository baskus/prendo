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
import random
import time

import config

class Country( db.Model ):
	location = db.StringProperty( required=True, multiline=False )
	
	@classmethod
	def save( cls, location ):
		memcache_str = "location:%s" % location
		
		if memcache.get( memcache_str ) is not None:
			return
		
		Country.get_or_insert( location, location=location )
		memcache.add( memcache_str, 1 )
	
	@classmethod
	def get_random_location( cls ):
		countries = Country.all().fetch( 1000 )
		l = len( countries )
		
		if l > 0:
			i = random.randint( 0, l - 1 )
			country = countries[i]
			return country.location
		else:
			return None
	
	@classmethod
	def next_country( cls ):
		fetched = Country.all().order( "location" ).fetch( 1000 )
		count = len( fetched )
		
		# Start from the 0th country if no index is saved.
		memcache.add( "country_index_next", 0 )
		
		index = memcache.get( "country_index_next" )
		if index >= count:
			index = 0
		
		location = fetched[index].location
		
		memcache.set( "country_index_next", index + 1 )
		
		return location
