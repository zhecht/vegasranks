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

def avg(a):
	return sum(a) / len(a)

def median(a):
	a = sorted(a)
	if len(a) % 2 != 0:
		return float(a[len(a) // 2])
	else:
		return (a[(len(a) // 2) - 1] + a[len(a) // 2]) / 2

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

@nfl_blueprint.route('/analyze')
def analyze_route():
	week = "3"

	with open(f"{prefix}static/nfl/stats.json") as fh:
		stats = json.load(fh)

	with open(f"{prefix}static/nfl/roster.json") as fh:
		roster = json.load(fh)

	ecr = getECR(week)
	vegas = getVegas(week)

	right = []
	posStatistics = {}
	ecrDiffAll = []
	vegasDiffAll = []
	ecrDiffAllPlusMinus = []
	vegasDiffAllPlusMinus = []
	table = []
	for pos in ["QB", "RB", "WR", "TE"]:
		posStatistics[pos] = {}

		actual = []
		for game in stats[week]:
			for player in stats[week][game]:
				away, home = map(str, game.split(" @ "))
				if player in roster[away]:
					p = roster[away][player]
				elif player in roster[home]:
					p = roster[home][player]
				else:
					continue

				if p != pos:
					continue

				pts = simpleCalcPoints(stats[week][game][player])
				actual.append((pts, player))
		
		ecrDiff = []
		vegasDiff = []
		ecrDiffPlusMinus = []
		vegasDiffPlusMinus = []
		ecrDiffOutliers = []
		vegasDiffOutliers = []
		topCount = {}
		cutoff = 48 # RB1->RB4
		if pos == "WR":
			cutoff = 72
		elif pos in ["QB", "TE"]:
			cutoff = 36
		for rank, row in enumerate(sorted(actual, reverse=True)):
			player = row[1]
			e = ecr[pos].get(player, '-')
			v = vegas[pos].get(player, '-')
			if e == "-" or v == "-":
				#print(f"{pos}{rank+1} {player.title()} vegas = {v}, ecr = {e}")
				table.append({
					"pos": pos,
					"rank": rank+1,
					"posRank": f"{pos}{rank+1}",
					"player": player.title(),
					"vegas": v,
					"ecr": e
				})
				continue

			# any difference, no outliers
			if abs(v-e) >= 0 and abs(rank+1-v) <= cutoff - 12 and abs(rank+1-e) <= cutoff - 12:
				vegasDiffOutliers.append(abs(rank+1 - v))
				ecrDiffOutliers.append(abs(rank+1 - e))	
			
			if abs(v-e) >= 3:
				vegasDiffPlusMinus.append(abs(rank+1 - v))
				ecrDiffPlusMinus.append(abs(rank+1 - e))
				vegasDiffAllPlusMinus.append(abs(rank+1 - v))
				ecrDiffAllPlusMinus.append(abs(rank+1 - e))

			vegasDiff.append(abs(rank+1 - v))
			ecrDiff.append(abs(rank+1 - e))
			vegasDiffAll.append(abs(rank+1 - v))
			ecrDiffAll.append(abs(rank+1 - e))
			right.append((abs(v-e), abs(rank+1 - v), abs(rank+1 - e), v, e, pos, player, rank))
				
				# percErr
				#vegasDiff.append(abs(rank+1 - v) / (rank+1))
				#ecrDiff.append(abs(rank+1 - e) / (rank+1))

			#print(f"{pos}{rank+1} {player.title()} vegas = {v}, ecr = {e}")
			table.append({
				"pos": pos,
				"rank": rank+1,
				"posRank": f"{pos}{rank+1}",
				"player": player.title(),
				"vegas": v,
				"ecr": e
			})

			if rank >= cutoff-1:
			#if rank >= 11:
				break

		#print(" ")
		#print(vegasDiff)
		#print(f"median={median(vegasDiff)}, mean={round(avg(vegasDiff), 2)}, stdev={round(statistics.stdev(vegasDiff), 2)}")
		#print(ecrDiff)
		#print(f"median={median(ecrDiff)}, mean={round(avg(ecrDiff), 2)}, stdev={round(statistics.stdev(ecrDiff), 2)}\n")

		posStatistics[pos] = {
			"vegasMedian": median(vegasDiff),
			"vegasMean": round(avg(vegasDiff), 2),
			"ecrMedian": median(ecrDiff),
			"ecrMean": round(avg(ecrDiff), 2),
			"vegasMedianPlusMinus": median(vegasDiffPlusMinus),
			"vegasMeanPlusMinus": round(avg(vegasDiffPlusMinus), 2),
			"ecrMedianPlusMinus": median(ecrDiffPlusMinus),
			"ecrMeanPlusMinus": round(avg(ecrDiffPlusMinus), 2),
		}

	posStatistics["ALL"] = {
		"vegasMedian": median(vegasDiffAll),
		"vegasMean": round(avg(vegasDiffAll), 2),
		"ecrMedian": median(ecrDiffAll),
		"ecrMean": round(avg(ecrDiffAll), 2),
		"vegasMedianPlusMinus": median(vegasDiffAllPlusMinus),
		"vegasMeanPlusMinus": round(avg(vegasDiffAllPlusMinus), 2),
		"ecrMedianPlusMinus": median(ecrDiffAllPlusMinus),
		"ecrMeanPlusMinus": round(avg(ecrDiffAllPlusMinus), 2),
	}

	best = []
	worst = []
	for projDiff, actualDiff, actualDiffECR, v, e, pos, player, rank in sorted(right, reverse=True):
		j = {
			"pos": pos,
			"rank": rank+1,
			"posRank": f"{pos}{rank+1}",
			"player": player.title(),
			"vegas": v,
			"ecr": e,
			"diff": projDiff,
			"actualDiff": actualDiff
		}
		if actualDiff < actualDiffECR:
			best.append(j)
		else:
			worst.append(j)

	return render_template("analyze.html", posStatistics=posStatistics, tableData=table, best=best, worst=worst)

def getECR(week):
	with open(f"{prefix}static/nfl/historical/wk{week}/fpros.json") as fh:
		fpros = json.load(fh)

	ecr = {}
	for pos in fpros:
		ecr[pos] = {}
		for player in fpros[pos]:
			try:
				if pos == "QB":
					ecr[pos][player] = fpros[pos][player]
				else:
					ecr[pos][player] = fpros[pos][player]["half"]
			except:
				continue
	return ecr

def getVegas(week):
	with open(f"{prefix}static/nfl/historical/wk{week}/ranksData.json") as fh:
		data = json.load(fh)

	with open(f"{prefix}static/nfl/roster.json") as fh:
		roster = json.load(fh)

	with open(f"{prefix}static/nfl/historical/wk{week}/fpros.json") as fh:
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
					p = calcPoints(prop, line * j[prop][line], "half")
					propPts[prop] += p
				pts += propPts[prop]

			#if player == "josh allen":
			#	print(pts, propPts)

			sortedOutputs[pos].append((pts, player, pos, team, propPts, j))
			sortedOutputs["ALL"].append((pts, player, pos, team, propPts, j))

	reddit = ""
	ranksTable = {}
	vegas = {}
	for pos in ["QB", "RB", "WR", "TE"]:
		props = ["attd", "rec", "rec_yd"]
		if pos == "QB":
			props = ["attd", "pass_td", "pass_yd", "int", "rush_yd"]
		elif pos == "RB":
			props = ["attd", "rush_yd", "rec", "rec_yd"]
		elif pos == "ALL":
			props = ["attd", "pass_td", "pass_yd", "int", "rush_yd", "rec", "rec_yd"]

		posIdx = {}
		vegas[pos] = {}
		for pts, player, p, team, propPts, j in sorted(sortedOutputs[pos], reverse=True):
			if p not in posIdx:
				posIdx[p] = 1

			onlyATTD = True
			for prop in props:
				if prop != "attd" and propPts.get(prop, 0):
					onlyATTD = False
					break

			if not onlyATTD:
				vegas[pos][player] = posIdx[p]

			posIdx[p] += 1

	return vegas

def simpleCalcPoints(j):
	pts = 0

	pts += int(j.get("rush_yd", "0")) * 0.1
	pts += int(j.get("rush_td", "0")) * 6
	pts += int(j.get("rec", "0")) * 0.5
	pts += int(j.get("rec_yd", "0")) * 0.1
	pts += int(j.get("rec_td", "0")) * 6
	pts += int(j.get("fumbles_lost", "0")) * -2
	pts += int(j.get("2pt", "0")) * 2
	return round(pts, 2)

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