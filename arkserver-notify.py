#!/usr/bin/python
#
# this script uses the Source RCON client lib converted to Python 3.7 by Elektordi
# https://gist.github.com/Elektordi/0132b4609d57b227a217232d2c6af80e

import datetime
import re
import requests
import sqlite3
import srcds

# Rename config-default.py to config.py and configure the variables in that file
import config

# Ark server specific variables, configured in config.py
rconIP = config.rconIP
rconPort = config.rconPort
rconPass = config.rconPass

# Telegram chat specific variables, configured in config.py
telegramBotToken = config.telegramBotToken
telegramBotChatID = config.telegramBotChatID

# Default script variables
notifyOfflineIntervalH = 4
dbName = 'ArkPlayerLog.db'
playerTable = 'ark_player_log'
statusTable = 'ark_server_status'
telegramDownMsg = 'Ark server seems to be down, rcon connect failed.'
telegramBaseUrl = ('https://api.telegram.org/bot' + telegramBotToken +
                   '/sendMessage?chat_id=' + telegramBotChatID + '&parse_mode=Markdown&text=')
testRconFile = 'rconOutput.txt'
arkServerId = 1

def connectDB():
    try:
        sqliteConnection = sqlite3.connect(dbName,detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
        # print("Connected to SQLite")
        return sqliteConnection
    except sqlite3.Error as error:
        print("Error connecting to database:", error)
        exit()

def createTable(con):
    sqlExists = f"SELECT COUNT(\"name\") FROM sqlite_master WHERE type='table' and name='{playerTable}'"
    sqlCreatePlayerTable = f"""CREATE TABLE '{playerTable}' (
                            steamId INTEGER PRIMARY KEY,
                            name TEXT,
                            last_logon TIMESTAMP,
                            last_logoff TIMESTAMP,
                            online_now BOOLEAN);"""
    sqlCreateStatusTable = f"""CREATE TABLE '{statusTable}' (
                            serverId INTEGER PRIMARY KEY,
                            checked_on TIMESTAMP,
                            last_online TIMESTAMP,
                            last_offline TIMESTAMP,
                            server_online BOOLEAN);"""
    sqlInsert = f"INSERT INTO \"{statusTable}\" (\"serverId\") VALUES (?);"
    cursor = con.cursor()
    try:
        # check if table exists
        cursor.execute(sqlExists)
        if cursor.fetchone()[0] == 1:
            # print(f"Table {dbTable} already exists, not recreating it")
            pass
        else:
            cursor.execute(sqlCreatePlayerTable)
            cursor.execute(sqlCreateStatusTable)
            cursor.execute(sqlInsert, (arkServerId,))
            con.commit()
            print(f"Created tables {playerTable} and {statusTable}")
    except sqlite3.Error as error:
        print(f"Error creating {playerTable} and/or {statusTable} table:", error)
    cursor.close()


def fetchRconPlayerList(con):
    online = 1
    try:
        rconServer = srcds.SourceRcon(rconIP, rconPort, rconPass)
        rconResult = rconServer.rcon('listplayers').decode("utf-8")
    except srcds.SourceRconError as error:
        online = 0
        print("Error retrieving playerlist via rcon: ", error)
        notifyServerDown()
        rconResult = "No Players Connected"
    if online == 1:
        sqlUpdate = f"""UPDATE \"{statusTable}\" SET \"checked_on\" = ?,
                            \"last_online\" = ?, \"server_online\" = ? WHERE \"serverId\" = ?"""
    else:
        sqlUpdate = f"""UPDATE \"{statusTable}\" SET \"checked_on\" = ?,
                            \"last_offline\" = ?, \"server_online\" = ? WHERE \"serverId\" = ?"""
    cursor = con.cursor()
    cursor.execute(sqlUpdate, (datetime.datetime.now(), datetime.datetime.now(), online, arkServerId))
    con.commit()
    cursor.close()
    # writeRconResultToFile(rconResult)
    return parseRconResult(rconResult)


def parseRconResult(rconResultStr):
    rconPlayerList = {}
    if 'No Players Connected' in rconResultStr:
        print("Server reports no players online")
        return rconPlayerList
    lines = rconResultStr.splitlines()
    for line in lines:
        result = re.search(r"(\d+)\. (.+), (\d+)", line)
        if result is not None:
            rconPlayerList[int(result.group(3))] = result.group(2)
    # testPrintDictionary(rconPlayerList)
    return rconPlayerList


def insertUpdatePlayersDB(con, rconPlayerList):
    sqlSelect = f"""SELECT * FROM \"{playerTable}\";"""
    cursor = con.cursor()
    cursor.execute(sqlSelect)
    # update players that are already in the db
    for row in cursor:
        # only update if online_now status has changed
        if row[0] in rconPlayerList.keys() and row[4] == 0:
            # player has come online
            updatePlayerRecord(con, {'steamid': row[0], 'name': row[1], 'online_now': 1})
            notifyPlayerOnline(row[1], 'online', row[3])
            del rconPlayerList[row[0]]
        elif row[0] not in rconPlayerList.keys() and row[4] == 1:
            # player has gone offline
            updatePlayerRecord(con, {'steamid': row[0], 'name': row[1], 'online_now': 0})
            notifyPlayerOffline(row[1], 'offline', row[2])
        elif row[0] in rconPlayerList.keys():
            # player is still online, no update in db needed
            del rconPlayerList[row[0]]
    # insert any remaining players in the Rcon list as new records
    for key, value in rconPlayerList.items():
        insertPlayerRecord(con, {'steamid': key, 'name': value})
        notifyPlayerOnline(value, 'online', None)


def insertPlayerRecord(con, playerInfo):
    print('Adding to db player ' + playerInfo['name'] + ' with steamid ' + str(playerInfo['steamid']))
    sqlInsert = f"""INSERT INTO \"{playerTable}\" (\"steamId\", \"name\", \"last_logon\", \"online_now\") 
                VALUES (?, ?, ?, ?);"""
    cursor = con.cursor()
    data = (playerInfo['steamid'], playerInfo['name'], datetime.datetime.now(), 1)
    cursor.execute(sqlInsert, data)
    con.commit()
    cursor.close()


def updatePlayerRecord(con, playerInfo):
    cursor = con.cursor()
    if playerInfo['online_now'] == 1:
        print('Now ONline: updating player ' + playerInfo['name'] + ' with steamid ' + str(playerInfo['steamid']))
        sqlUpdate = f"UPDATE \"{playerTable}\" SET \"last_logon\" = ?, \"online_now\" = ? WHERE \"steamId\" = ?"
    else:
        print('Now OFFline: updating player ' + playerInfo['name'] + ' with steamid ' + str(playerInfo['steamid']))
        sqlUpdate = f"UPDATE \"{playerTable}\" SET \"last_logoff\" = ?, \"online_now\" = ? WHERE \"steamId\" = ?;"
    try:
        cursor.execute(sqlUpdate, (datetime.datetime.now(), playerInfo['online_now'], playerInfo['steamid']))
        con.commit()
    except sqlite3.Error as error:
        print("Error updating player info:", error)
    cursor.close()


def writeRconResultToFile(rconResult):
    f = open(testRconFile, 'w')
    f.write(rconResult.decode("utf-8"))
    f.close()


def testPrintDictionary(dict):
    for key, value in dict.items():
        print(str(key) + ': ' + str(value))


def testFetchRConPlayerListFile():
    try:
        file = open(testRconFile, 'r')
    except IOError as error:
        print(f"Error reading rcon test file  {testRconFile}:", error)
        exit()
    rconResultStr = file.read()
    return parseRconResult(rconResultStr)


def testAddPlayersDB(con):
    # https://pynative.com/python-sqlite-date-and-datetime/
    playerList = {}
    playerList[76561190000000001] = "Ark noob 1"
    playerList[76561190000000002] = "Ark master 1"
    #playerList[76561190000000003] = "Ark noob 2"
    playerList[76561190000000004] = "Ark master 2"
    sqlInsert = f"""INSERT INTO \"{playerTable}\"
                (\"steamId\", \"name\", \"last_logon\", \"last_logoff\", \"online_now\") 
                VALUES (?, ?, ?, ?, ?);"""
    cursor = con.cursor()
    for key, value in playerList.items():
        data = (key, value, datetime.datetime.now(), datetime.datetime.now(), 1)
        cursor.execute(sqlInsert, data)
    con.commit()
    cursor.close()


def testListPlayersDB(con):
    print('===== Players in database:')
    print('SteamID - Name - Last Logon - Last Logoff - Online now')
    sqlSelect = f"SELECT * FROM \"{playerTable}\""
    cursor = con.cursor()
    cursor.execute(sqlSelect)
    records = cursor.fetchall()
    for row in records:
        print(str(row[0]) + " - " + str(row[1]) + " - " + str(row[2]) + " - " + str(row[3]) + " - " + str(row[4]))
    cursor.close()
    print('=====')

def notifyServerDown():
    sendTelegramMsg(telegramBaseUrl + telegramDownMsg)


def notifyPlayerOnline(name, status, lastLogOff):
    msg = f"Ark player {name} is now {status}."
    if lastLogOff is not None:
        offlineTime = lastLogOff.strftime("%H:%M")
        if datetime.datetime.now().strftime("%Y%m%d") == lastLogOff.strftime("%Y%m%d"):
            msg += f" Player went last offline today at {offlineTime}"
        elif (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y%m%d") == lastLogOff.strftime("%Y%m%d"):
            msg += f" Player went last offline yesterday at {offlineTime}"
        else:
            offlineDaysAgo = (datetime.datetime.now() - lastLogOff).days
            offlineDate = lastLogOff.strftime("%A %d %b %Y %H:%M")
            msg += f" Player went last offline on {offlineDate}, {offlineDaysAgo} days ago"
    # print(msg)
    sendTelegramMsg(telegramBaseUrl + msg)


def notifyPlayerOffline(name, status, lastLogon):
    msg = f"Ark player {name} is now {status}."
    if lastLogon is not None:
        timeOnline = ':'.join(str(datetime.datetime.now() - lastLogon).split(':')[:2])
        msg += f" Player was online for {timeOnline}."
    # print(msg)
    sendTelegramMsg(telegramBaseUrl + msg)

def sendTelegramMsg(sendText):
    try:
        response = requests.get(sendText)
    except requests.exceptions.RequestException as error:
        print("Error sending Telegram notification: ", error)


def cleanAndClose(con):
    con.close()
    exit()



con = connectDB()
#con.set_trace_callback(print)
createTable(con)
# testListPlayersDB(con)
# fetchRconPlayerList()

insertUpdatePlayersDB(con, fetchRconPlayerList(con))
#insertUpdatePlayersDB(con, testFetchRConPlayerListFile(con))

# testAddPlayersDB(con)
# testListPlayersDB(con)

cleanAndClose(con)
