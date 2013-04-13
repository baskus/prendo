/*
 * prendo.h
 *
 * Copyright (c) 2013 Rickard Edström
 * Copyright (c) 2013 Sebastian Ärleryd
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

#ifndef __PRENDO_H__
#define __PRENDO_H__

#include <memory>
#include <vector>
#include <algorithm>
#include <tuple>
#if (CC_TARGET_PLATFORM == CC_PLATFORM_IOS)
#include "curl.h"
#else
#include <curl/curl.h>
#endif
#include "cocos2d.h"
#include "cocos-ext.h"
#include "picojson.h"

struct ScoreEntry {
	std::string _name;
	std::string _comment;
	long _points;
	// The date in unix time the score was made.
	long _date;	
	
	std::string _control;
	std::string _location;
	
	ScoreEntry(picojson::value json) {
		auto j = json.get<picojson::object>();
		
		_name = j["name"].get<std::string>();
		_comment = j["comment"].get<std::string>();
		_points = j["points"].get<double>();
		_control = j["control"].get<std::string>();
		if ( j.count( "date" ) > 0 ) {
			_date = j["date"].get<double>();
		} else {
			_date = 0;
		}
		if ( j.count( "location" ) > 0 ) {
			_location = j["location"].get<std::string>();
		} else {
			_location = "";
		}
	}
	
	ScoreEntry( std::string name, std::string comment, long points,
		std::string control )
	: _name(name)
	, _comment(comment)
	, _points(points)
	, _date( 0 )
	, _control(control)
	, _location(""){
	}
	
	picojson::value toJSON() {
		std::map<std::string, picojson::value> map = {
			{"name", picojson::value(_name)},
			{"comment", picojson::value(_comment)},
			{"points", picojson::value((double)_points)},
			{"control", picojson::value(_control)}
		};
		
		return picojson::value(map);
	}
};

typedef std::shared_ptr<std::vector<ScoreEntry>> pvse;
typedef std::tuple<pvse, pvse, pvse> tup;

class Scores {
	static void sortScores(std::vector<ScoreEntry>& s) {
		std::sort( s.begin(), s.end(), [] ( const ScoreEntry & lhs,
				const ScoreEntry & rhs ) {
			return lhs._points > rhs._points;
		});
	}
	
	static tup handleRequest(picojson::value response_value) {
		auto response_obj = response_value.get<picojson::object>();
		auto request_obj = response_obj["request"].get<picojson::object>();
		
		const std::string LOCATION_WORLD = "location_world";
		const std::string LOCATION_WEEK = "location_week";
		
		// TODO : handle control maybe
		//auto control = request_obj["control"].get<std::string>();
		
		auto requestData = request_obj["data"].get<picojson::array>();
		
		pvse newWorld = nullptr;
		pvse newNational = nullptr;
		pvse newWeek = nullptr;
		
		for(auto& v : requestData) {
			auto list_string = v.get<std::string>();
			auto list_v = jsonParse(list_string);
			auto list_obj = list_v.get<picojson::object>();
			auto listLocation = list_obj["location"].get<std::string>();
			auto listScores = list_obj["scores"].get<picojson::array>();
			
			auto s = extractScores(listScores);
			
			sortScores( s );
			
			// TODO case insensitive compare?
			if(listLocation == LOCATION_WORLD) {
				std::vector< ScoreEntry > * kaka =
					new std::vector< ScoreEntry >( s );
				newWorld.reset( kaka );
			}
			// TODO case insensitive compare?
			else if(listLocation == LOCATION_WEEK) {
				newWeek.reset( new std::vector< ScoreEntry >( s ) );
			}
			// Two character country code.
			else if(listLocation.length() == 2){
				newNational.reset( new std::vector< ScoreEntry >( s ) );
			}
		}
		
		return std::make_tuple(newWorld, newNational, newWeek);
	}
	
public:
	static picojson::value jsonParse(std::string s) {
		auto json = s.c_str();
		std::string error;
		picojson::value v;
		picojson::parse(v, json, json+strlen(json), &error);
		if (!error.empty()) {
			// todo throw(...);
		}
		return v;
	}
	
	static std::vector<ScoreEntry> extractScores(picojson::array arr) {
		std::vector<ScoreEntry> to_return;
		
		for(auto& json : arr) {
			to_return.push_back(ScoreEntry(json));
		}
		
		return to_return;
	}
	static tup handleJson(std::string json) {
		
		return handleRequest(jsonParse(json));
	}
};

class ScoreManager : public cocos2d::CCObject {

	pvse _world;
	pvse _national;
	pvse _week;
	bool _refreshInProgress;
	std::vector<ScoreEntry> _submitQueue;
	std::vector<ScoreEntry> _submitQueueInProgress;

	std::function< void() > _refreshCompleteCallback;
	
	ScoreManager() : _refreshInProgress( false ) {
		auto s = cocos2d::CCUserDefault::sharedUserDefault()->getStringForKey(
			"__prendo_saved_scores" );
		if (s != "") {
			_submitQueue = Scores::extractScores(
				Scores::jsonParse( s ).get< picojson::array >() );
		}
	}
	
	void onHttpRequestCompleted(cocos2d::CCNode *sender, void *data) {
		cocos2d::extension::CCHttpResponse *response =
			static_cast< cocos2d::extension::CCHttpResponse * >( data );
		
		if (!response || !response->isSucceed())
		{
			_refreshInProgress = false;
			for(auto& se : _submitQueueInProgress) {
				_submitQueue.push_back(se);
			}
			_submitQueueInProgress.clear();
			return;
		}
		
		std::vector<char>* v = response->getResponseData();
		
		std::string str(v->begin(),v->end());
		
		auto t = Scores::handleJson(str);
		auto scoresWorld = std::get<0>(t);
		auto scoresNational = std::get<1>(t);
		auto scoresWeek= std::get<2>(t);
		
		if (scoresWorld)
			_world = scoresWorld;
		
		if (scoresNational)
			_national = scoresNational;
		
		if (scoresWeek)
			_week = scoresWeek;
		
		_refreshInProgress = false;
		_submitQueueInProgress.clear();
		saveQueue();
		
		_refreshCompleteCallback();
	}
	
	std::string prepareData(std::string control) {
		const std::string SECRET_SUBMIT_CODE = "<SECRET SUBMIT CODE HERE>";
		picojson::object json_request = {
			{"control", picojson::value(control)}
		};
		
		picojson::array json_scores_array;
		for(auto& e : _submitQueueInProgress) {
			json_scores_array.push_back(e.toJSON());
		}
		picojson::object json_submit = {
			{"code", picojson::value(SECRET_SUBMIT_CODE)},
			{"scores", picojson::value(json_scores_array)}
		};
		
		picojson::object json_data = {
			{"request", picojson::value(json_request)},
			{"submit", picojson::value(json_submit)}
		};
		return picojson::value(json_data).serialize();
	}
	void saveQueue() {
		picojson::array json_scores_array;
		for(auto& e : _submitQueueInProgress) {
			json_scores_array.push_back(e.toJSON());
		}
		for(auto& e : _submitQueue) {
			json_scores_array.push_back(e.toJSON());
		}
		
		auto ud = cocos2d::CCUserDefault::sharedUserDefault();
		ud->setStringForKey( "__prendo_saved_scores",
			picojson::value( json_scores_array ).serialize() );
		ud->flush();
	}
public:	
	static ScoreManager* sharedScoreManager() {
		static ScoreManager* _instance;

		if (_instance == NULL)
			_instance = new ScoreManager();
		
		return _instance;
	}
	
	pvse getWorldList() {
		return _world;
	}
	
	pvse getNationalList() {
		return _national;
	}
	
	pvse getWeekList() {
		return _week;
	}
	
	void submitScore( ScoreEntry e ) {
		_submitQueue.push_back( e );
		
		saveQueue();
	}
	
	void requestRefresh( const std::string & control,
						std::function< void() > callback ) {
		_refreshCompleteCallback = callback;
		const std::string URL_BASE = "http://prendo-test.appspot.com";
		const std::string URL_REQ_AND_SUB = URL_BASE + "/ras";
		
		if ( !_refreshInProgress ) {
			_refreshInProgress = true;
			for(auto& se : _submitQueue) {
				_submitQueueInProgress.push_back(se);
			}
			_submitQueue.clear();
			
			auto request = new cocos2d::extension::CCHttpRequest();
			request->setRequestType(
				cocos2d::extension::CCHttpRequest::kHttpPost );
			{
				using namespace cocos2d;
				request->setResponseCallback(this,
					callfuncND_selector(ScoreManager::onHttpRequestCompleted));
			}
			request->setUrl(URL_REQ_AND_SUB.c_str());
			
			auto s = prepareData(control);
			
			CCLOG( "ScoreManager: Going to send: %s", s.c_str() );
			
			auto curl = curl_easy_init();
			auto escaped = curl_easy_escape(curl, s.c_str(), s.length());
			
			char* s2;
			asprintf(&s2, "data=%s", escaped);
			
			curl_free(escaped);
			curl_easy_cleanup(curl);
			auto l = strlen(s2);
			
			request->setRequestData( s2, l );
			cocos2d::extension::CCHttpClient::getInstance()->send(request);
			free(s2);
			request->release();
			
			CCLOG( "ScoreManager: request sent." );
		}
	}
};

#endif // __PRENDO_H__
