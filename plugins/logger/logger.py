#!/usr/bin/env python
##      plugins/logger.py
##
## Gajim Team:
##      - Yann Le Boulanger <asterix@crans.org>
##      - Vincent Hanquez <tab@tuxfamily.org>
##
##      Copyright (C) 2003 Gajim Team
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published
## by the Free Software Foundation; version 2 only.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##

import os
import string
import time
import common.optparser
CONFPATH = "~/.gajim/config"
LOGPATH = os.path.expanduser("~/.gajim/logs/")

class plugin:
	def read_queue(self):
		while 1:
			while self.queueIN.empty() == 0:
				if self.config.has_key('lognotsep'):
					lognotsep = self.config['lognotsep']
				else:
					lognotsep = 1
				if self.config.has_key('lognotusr'):
					lognotusr = self.config['lognotusr']
				else:
					lognotusr = 1
#				tim = time.strftime("%d%m%y%H%M%S")
				tim = "%d" % time.time()
				
				ev = self.queueIN.get()
				if ev[0] == 'QUIT':
					print "plugin logger stopped"
					return
				elif ev[0] == 'NOTIFY':
					status = ev[1][2]
					jid = string.split(ev[1][0], '/')[0]
					if not status:
						status = ""
					if lognotsep == 1:
						fic = open(LOGPATH + "notify.log", "a")
						fic.write("%s:%s:%s:%s\n" % (tim, ev[1][0], \
							ev[1][1], status))
						fic.close()
					if lognotusr == 1:
						fic = open(LOGPATH + jid, "a")
						fic.write("%s:%s:%s:%s\n" % (tim, jid, \
							ev[1][1], status))
						fic.close()
				elif ev[0] == 'MSG':
					jid = string.split(ev[1][0], '/')[0]
					fic = open(LOGPATH + jid, "a")
					fic.write("%s:recv:%s\n" % (tim, ev[1][1]))
					fic.close()
				elif ev[0] == 'MSGSENT':
					jid = string.split(ev[1][0], '/')[0]
					fic = open(LOGPATH + jid, "a")
					fic.write("%s:sent:%s\n" % (tim, ev[1][1]))
					fic.close()
			time.sleep(0.5)

	def wait(self, what):
		"""Wait for a message from Core"""
		#TODO: timeout, save messages that don't fit
		while 1:
			if not self.queueIN.empty():
				ev = self.queueIN.get()
				if ev[0] == what and ev[1][0] == 'Logger':
					return ev[1][1]
			time.sleep(0.1)

	def __init__(self, quIN, quOUT):
		self.queueIN = quIN
		self.queueOUT = quOUT
		quOUT.put(('ASK_CONFIG', ('Logger', 'Logger')))
		self.config = self.wait('CONFIG')
		#create ~/.gajim/logs/ if it doesn't exist
		try:
			os.stat(os.path.expanduser("~/.gajim"))
		except OSError:
			os.mkdir(os.path.expanduser("~/.gajim"))
			print "creating ~/.gajim/"
		try:
			os.stat(LOGPATH)
		except OSError:
			os.mkdir(LOGPATH)
			print "creating ~/.gajim/logs/"
		self.read_queue()
		

if __name__ == "__main__":
	plugin(None, None)

print "plugin logger loaded"
