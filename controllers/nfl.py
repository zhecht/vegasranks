from flask import *
from datetime import datetime,timedelta
from subprocess import call
from bs4 import BeautifulSoup as BS
import math
import json
import os
import re
import argparse
import unicodedata
import time
from twilio.rest import Client

nfl_blueprint = Blueprint('nfl', __name__, template_folder='views')

prefix = ""
if os.path.exists("/home/vegasranks/vegasranks"):
	# if on linux aka prod
	prefix = "/home/vegasranks/vegasranks/"

def convertDecOdds(odds):
	if odds == 0:
		return 0
	if odds > 0:
		decOdds = 1 + (odds / 100)
	else:
		decOdds = 1 - (100 / odds)
	return decOdds

def convertAmericanOdds(avg):
	if avg >= 2:
		avg = (avg - 1) * 100
	else:
		avg = -100 / (avg - 1)
	return round(avg)

def averageOdds(odds):
	avgOver = []
	avgUnder = []
	for o in odds:
		if o and o != "-" and o.split("/")[0] != "-":
			avgOver.append(convertDecOdds(int(o.split("/")[0])))
			if "/" in o:
				avgUnder.append(convertDecOdds(int(o.split("/")[1])))

	if avgOver:
		avgOver = float(sum(avgOver) / len(avgOver))
		avgOver = convertAmericanOdds(avgOver)
	else:
		avgOver = "-"
	if avgUnder:
		avgUnder = float(sum(avgUnder) / len(avgUnder))
		avgUnder = convertAmericanOdds(avgUnder)
	else:
		avgUnder = "-"

	ou = f"{avgOver}/{avgUnder}"
	if ou.endswith("/-"):
		ou = ou.split("/")[0]
	return ou

def getFairValue(ou, method=None):
	over = int(ou.split("/")[0])
	if over > 0:
		impliedOver = 100 / (over+100)
	else:
		impliedOver = -1*over / (-1*over+100)

	# assume 7.1% vig if no under
	if "/" not in ou:
		u = 1.071 - impliedOver
		if u > 1:
			return
		if over > 0:
			under = int((100*u) / (-1+u))
		else:
			under = int((100 - 100*u) / u)
	else:
		under = int(ou.split("/")[1])

	if under > 0:
		impliedUnder = 100 / (under+100)
	else:
		impliedUnder = -1*under / (-1*under+100)

	# power method
	x = impliedOver
	y = impliedUnder
	while round(x+y, 8) != 1.0:
		k = math.log(2) / math.log(2 / (x+y))
		x = x**k
		y = y**k

	mult = impliedOver / (impliedOver + impliedUnder)
	add = impliedOver - (impliedOver+impliedUnder-1) / 2
	implied = min(x,mult,add)
	if method == "mult":
		return mult
	elif method == "add":
		return add
	elif method == "power":
		return x
	return implied

@nfl_blueprint.route('/getVegasRanks')
def getVegasRanks_route():
	propArg = request.args.get("prop")
	formatArg = request.args.get("format")

	res = []

	with open(f"{prefix}static/nfl/ranksData.json") as fh:
		data = json.load(fh)

	with open(f"{prefix}static/nfl/roster.json") as fh:
		roster = json.load(fh)

	with open(f"{prefix}static/nfl/fpros.json") as fh:
		fpros = json.load(fh)

	sortedOutputs = {"ALL": []}
	for team in data:
		for player in data[team]:
			pos = roster[team][player]
			if pos not in sortedOutputs:
				sortedOutputs[pos] = []
			j = {}
			for prop in data[team][player]:
				arr = []
				for line in data[team][player][prop]:
					odds = data[team][player][prop][line]
					l = []
					for o in odds:
						implied = getFairValue(o)
						l.append(implied)
					l = sorted(l)
					avgOdds = averageOdds(odds)
					#avgImplied = sum(l) / len(l)

					#if player == "jalen hurts" and prop == "pass_td":
					#	print(line, avgOdds)
					
					#print(player, prop, line)
					arr.append((math.ceil(float(line)), getFairValue(avgOdds, method="power"), avgOdds))
					#arr.append((abs(0.5-avgImplied), len(odds), line, avgOdds, avgImplied))

				if not arr:
					continue

				arr = sorted(arr, reverse=True)

				j[prop] = {}
				tot = last = 0
				for line, implied, avg in arr:
					if not implied:
						implied = .002
					tot += (implied - last)
					j[prop][line] = implied - last
					last = implied

				j[prop][0] = 1 - tot

			pts = 0
			propPts = {}
			for prop in j:
				propPts[prop] = 0
				for line in j[prop]:
					p = calcPoints(prop, line * j[prop][line], formatArg)
					propPts[prop] += p
				pts += propPts[prop]

			#if player == "josh allen":
			#	print(pts, propPts)

			sortedOutputs[pos].append((pts, player, pos, team, propPts, j))
			sortedOutputs["ALL"].append((pts, player, pos, team, propPts, j))

	reddit = ""
	ranksTable = {}
	for pos in ["ALL", "QB", "RB", "WR", "TE"]:
		if pos != propArg.upper():
			continue
		output = "\tPTS\tFPros\tPLAYER\tOPP"
		reddit += "PTS|PLAYER"
		props = ["attd", "rec", "rec_yd"]
		if pos == "QB":
			props = ["attd", "pass_td", "pass_yd", "int", "rush_yd"]
		elif pos == "RB":
			props = ["attd", "rush_yd", "rec", "rec_yd"]
		elif pos == "ALL":
			props = ["attd", "pass_td", "pass_yd", "int", "rush_yd", "rec", "rec_yd"]

		for prop in props:
			output += f"\t{prop.upper()}"
		output += "\n"
		reddit += "\n"

		posIdx = {}
		for pts, player, p, team, propPts, j in sorted(sortedOutputs[pos], reverse=True):
			if p not in posIdx:
				posIdx[p] = 1
			x = f"{p}{posIdx[p]}"
			
			fpDiff = "-"
			if player in fpros[p] and (p == "QB" or formatArg in fpros[p][player]):
				if p == "QB":
					fpDiff = fpros[p][player] - posIdx[p]
				else:
					fpDiff = fpros[p][player][formatArg] - posIdx[p]
				if fpDiff > 0:
					fpDiff = f"+{fpDiff}"
				else:
					fpDiff = str(fpDiff)

			j = {
				"player": player.title(),
				"pos": p,
				"rank": x,
				"pts": round(pts, 1),
				#"fpros": fpros[team].get(player, "-"),
				"fpDiff": fpDiff,
				#"opp": opps.get(team, "-").upper(),
			}

			for prop in props:
				x = 0
				if prop in propPts:
					x = round(propPts[prop], 2)
				j[f"{prop}"] = x

			res.append(j)
			posIdx[p] += 1

	return jsonify(res)

@nfl_blueprint.route('/ranks')
def ranks_route():
	return render_template("ranks.html")

def calcPoints(prop, val, format_="half"):
	pts = 0
	if prop == "rec":
		if format_ == "std":
			pts += val * 0.0
		elif format_ == "half":
			pts += val * 0.5
		else:
			pts += val * 1.0
	elif prop in ["rec_yd", "rush_yd"]:
		pts += val * 0.1
	elif prop == "pass_yd":
		pts += val * 0.04
	elif prop == "pass_td":
		pts += val * 4
	elif prop in ["attd", "2+td", "3+td"]:
		pts += val * 6
	elif prop == "int":
		pts += val * -2
	return pts