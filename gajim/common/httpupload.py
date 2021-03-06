# -*- coding: utf-8 -*-
#
# This file is part of Gajim.
#
# Gajim is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published
# by the Free Software Foundation; version 3 only.
#
# Gajim is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Gajim.  If not, see <http://www.gnu.org/licenses/>.

import os
import threading
import ssl
import urllib
from urllib.request import Request, urlopen
from urllib.parse import urlparse, quote
import io
import mimetypes
import logging

import nbxmpp
from nbxmpp import NS_HTTPUPLOAD
from gi.repository import GLib

from gajim.common import app
from gajim.common import ged
from gajim.common.connection_handlers_events import InformationEvent
from gajim.common.connection_handlers_events import HTTPUploadProgressEvent
from gajim.common.connection_handlers_events import MessageOutgoingEvent
from gajim.common.connection_handlers_events import GcMessageOutgoingEvent

if os.name == 'nt':
    import certifi

log = logging.getLogger('gajim.c.httpupload')


class ConnectionHTTPUpload:
    """
    Implement HTTP File Upload
    (XEP-0363, https://xmpp.org/extensions/xep-0363.html)
    """
    def __init__(self):
        self.httpupload = False
        self.encrypted_upload = False
        self.component = None
        self.max_file_size = None  # maximum file size in bytes

        app.ged.register_event_handler('agent-info-received',
                                       ged.GUI1,
                                       self.handle_agent_info_received)
        app.ged.register_event_handler('stanza-message-outgoing',
                                       ged.OUT_PREGUI,
                                       self.handle_outgoing_stanza)
        app.ged.register_event_handler('gc-stanza-message-outgoing',
                                       ged.OUT_PREGUI,
                                       self.handle_outgoing_stanza)

        self.messages = []

    def cleanup(self):
        app.ged.remove_event_handler('agent-info-received',
                                     ged.GUI1,
                                     self.handle_agent_info_received)
        app.ged.remove_event_handler('stanza-message-outgoing',
                                     ged.OUT_PREGUI,
                                     self.handle_outgoing_stanza)
        app.ged.remove_event_handler('gc-stanza-message-outgoing',
                                     ged.OUT_PREGUI,
                                     self.handle_outgoing_stanza)

    def handle_agent_info_received(self, event):
        if (NS_HTTPUPLOAD not in event.features or not
                app.jid_is_transport(event.jid)):
            return

        if not event.id_.startswith('Gajim_'):
            return

        account = event.conn.name
        if account != self.name:
            return

        self.component = event.jid

        for form in event.data:
            form_dict = form.asDict()
            if form_dict.get('FORM_TYPE', None) != NS_HTTPUPLOAD:
                continue
            size = form_dict.get('max-file-size', None)
            if size is not None:
                self.max_file_size = int(size)
                break

        if self.max_file_size is None:
            log.warning('%s does not provide maximum file size', account)
        else:
            log.info('%s has a maximum file size of: %s MiB',
                     account, self.max_file_size/(1024*1024))

        self.httpupload = True
        for ctrl in app.interface.msg_win_mgr.get_controls(acct=self.name):
            ctrl.update_actions()

    def handle_outgoing_stanza(self, event):
        if event.conn.name != self.name:
            return
        message = event.msg_iq.getTagData('body')
        if message and message in self.messages:
            self.messages.remove(message)
            # Add oob information before sending message to recipient,
            #  to distinguish HTTP File Upload Link from pasted URL
            oob = event.msg_iq.addChild('x', namespace=nbxmpp.NS_X_OOB)
            oob.addChild('url').setData(message)
            if 'gajim' in event.additional_data:
                event.additional_data['gajim']['oob_url'] = message
            else:
                event.additional_data['gajim'] = {'oob_url': message}

    def check_file_before_transfer(self, path, encryption, contact, session,
                                   groupchat=False):
        if not path or not os.path.exists(path):
            return

        invalid_file = False
        stat = os.stat(path)

        if os.path.isfile(path):
            if stat[6] == 0:
                invalid_file = True
                msg = _('File is empty')
        else:
            invalid_file = True
            msg = _('File does not exist')

        if self.max_file_size is not None and \
                stat.st_size > self.max_file_size:
            invalid_file = True
            size = GLib.format_size_full(self.max_file_size,
                                         GLib.FormatSizeFlags.IEC_UNITS)
            msg = _('File is too large, '
                    'maximum allowed file size is: %s') % size

        if invalid_file:
            self.raise_information_event('open-file-error2', msg)
            return

        mime = mimetypes.MimeTypes().guess_type(path)[0]
        if not mime:
            mime = 'application/octet-stream'  # fallback mime type
        log.info("Detected MIME type of file: %s", mime)

        try:
            file = File(path, contact, mime=mime, encryption=encryption,
                        update_progress=self.raise_progress_event,
                        session=session, groupchat=groupchat)
            app.interface.show_httpupload_progress(file)
        except Exception as error:
            log.exception('Error while loading file')
            self.raise_information_event('open-file-error2', str(error))
            return

        if encryption is not None:
            app.interface.encrypt_file(file, self.request_slot)
        else:
            self.request_slot(file)

    @staticmethod
    def raise_progress_event(status, file, seen=None, total=None):
        app.nec.push_incoming_event(HTTPUploadProgressEvent(
            None, status=status, file=file, seen=seen, total=total))

    @staticmethod
    def raise_information_event(dialog_name, args=None):
        app.nec.push_incoming_event(InformationEvent(
            None, dialog_name=dialog_name, args=args))

    def request_slot(self, file):
        GLib.idle_add(self.raise_progress_event, 'request', file)
        iq = nbxmpp.Iq(typ='get', to=self.component)
        id_ = app.get_an_id()
        iq.setID(id_)
        request = iq.setTag(name="request", namespace=NS_HTTPUPLOAD)
        request.addChild('filename', payload=quote(os.path.basename(file.path)))
        request.addChild('size', payload=file.size)
        request.addChild('content-type', payload=file.mime)

        log.info("Sending request for slot")
        app.connections[self.name].connection.SendAndCallForResponse(
            iq, self.received_slot, {'file': file})

    @staticmethod
    def get_slot_error_message(stanza):
        tmp = stanza.getTag('error').getTag('file-too-large')

        if tmp is not None:
            max_file_size = int(tmp.getTag('max-file-size').getData())
            return _('File is too large, maximum allowed file size is: %s') % \
                GLib.format_size_full(max_file_size,
                                      GLib.FormatSizeFlags.IEC_UNITS)

        return stanza.getErrorMsg()

    def received_slot(self, conn, stanza, file):
        log.info("Received slot")
        if stanza.getType() == 'error':
            self.raise_progress_event('close', file)
            self.raise_information_event('request-upload-slot-error',
                                         self.get_slot_error_message(stanza))
            log.error(stanza)
            return

        try:
            file.put = stanza.getTag("slot").getTag("put").getData()
            file.get = stanza.getTag("slot").getTag("get").getData()
        except Exception:
            log.error("Got unexpected stanza: %s", stanza)
            log.exception('Error')
            self.raise_progress_event('close', file)
            self.raise_information_event('request-upload-slot-error2')
            return

        if (urlparse(file.put).scheme != 'https' or
                urlparse(file.get).scheme != 'https'):
            self.raise_progress_event('close', file)
            self.raise_information_event('unsecure-error')
            return

        try:
            file.stream = StreamFileWithProgress(file)
        except Exception:
            log.exception('Error')
            self.raise_progress_event('close', file)
            self.raise_information_event('open-file-error')
            return

        log.info('Uploading file to %s', file.put)
        log.info('Please download from %s', file.get)

        thread = threading.Thread(target=self.upload_file, args=(file,))
        thread.daemon = True
        thread.start()

    def upload_file(self, file):
        GLib.idle_add(self.raise_progress_event, 'upload', file)
        try:
            headers = {'User-Agent': 'Gajim %s' % app.version,
                       'Content-Type': file.mime,
                       'Content-Length': file.size}

            request = Request(
                file.put, data=file.stream, headers=headers, method='PUT')
            log.info("Opening Urllib upload request...")

            if not app.config.get_per('accounts', self.name, 'httpupload_verify'):
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                log.warning('CERT Verification disabled')
                transfer = urlopen(request, timeout=30, context=context)
            else:
                if os.name == 'nt':
                    transfer = urlopen(
                        request, cafile=certifi.where(), timeout=30)
                else:
                    transfer = urlopen(request, timeout=30)
            file.stream.close()
            log.info('Urllib upload request done, response code: %s',
                     transfer.getcode())
            GLib.idle_add(self.upload_complete, transfer.getcode(), file)
            return
        except UploadAbortedException as exc:
            log.info(exc)
            error_msg = exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLError):
                error_msg = exc.reason.reason
                if error_msg == 'CERTIFICATE_VERIFY_FAILED':
                    log.exception('Certificate verify failed')
            else:
                log.exception('URLError')
                error_msg = exc.reason
        except Exception as exc:
            log.exception("Exception during upload")
            error_msg = exc
        GLib.idle_add(self.raise_progress_event, 'close', file)
        GLib.idle_add(self.on_upload_error, file, error_msg)

    def upload_complete(self, response_code, file):
        self.raise_progress_event('close', file)
        if 200 <= response_code < 300:
            log.info("Upload completed successfully")
            message = file.get
            if file.user_data:
                message += '#' + file.user_data
                message = self.convert_to_aegscm(message)
            else:
                self.messages.append(message)

            if file.groupchat:
                app.nec.push_outgoing_event(GcMessageOutgoingEvent(
                    None, account=self.name, jid=file.contact.jid,
                    message=message, automatic_message=False,
                    session=file.session))
            else:
                app.nec.push_outgoing_event(MessageOutgoingEvent(
                    None, account=self.name, jid=file.contact.jid,
                    message=message, keyID=file.keyID, type_='chat',
                    automatic_message=False, session=file.session))

        else:
            log.error('Got unexpected http upload response code: %s',
                      response_code)
            self.raise_information_event('httpupload-response-error',
                                         response_code)

    def on_upload_error(self, file, reason):
        self.raise_progress_event('close', file)
        self.raise_information_event('httpupload-error', str(reason))

    @staticmethod
    def convert_to_aegscm(url):
        return 'aesgcm' + url[5:]


class File:
    def __init__(self, path, contact, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.encrypted = False
        self.contact = contact
        self.keyID = None
        if hasattr(contact, 'keyID'):
            self.keyID = contact.keyID
        self.stream = None
        self.path = path
        self.put = None
        self.get = None
        self.data = None
        self.user_data = None
        self.size = None
        self.event = threading.Event()
        self.load_data()

    def load_data(self):
        with open(self.path, 'rb') as content:
            self.data = content.read()
        self.size = len(self.data)

    def get_data(self, full=False):
        if full:
            return io.BytesIO(self.data).getvalue()
        return io.BytesIO(self.data)


class StreamFileWithProgress:
    def __init__(self, file):
        self.file = file
        self.event = file.event
        self.backing = file.get_data()
        self.backing.seek(0, os.SEEK_END)
        self._total = self.backing.tell()
        self.backing.seek(0)
        self._callback = file.update_progress
        self._seen = 0

    def __len__(self):
        return self._total

    def read(self, size):
        if self.event.isSet():
            raise UploadAbortedException

        data = self.backing.read(size)
        self._seen += len(data)
        if self._callback:
            GLib.idle_add(self._callback, 'update',
                          self.file, self._seen, self._total)
        return data

    def close(self):
        return self.backing.close()


class UploadAbortedException(Exception):
    def __str__(self):
        return "Upload Aborted"
