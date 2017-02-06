# Copyright (C) 2013-2017 Jean-Francois Romang (jromang@posteo.de)
#                         Shivkumar Shivaji ()
#                         Jürgen Précour (LocutusOfPenguin@posteo.de)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import serial as pyserial
import struct
from utilities import *
from threading import Timer, Lock
from fcntl import fcntl, F_GETFL, F_SETFL
from os import O_NONBLOCK, read, path
import subprocess
import time

try:
    import enum
except ImportError:
    import enum34 as enum


class DgtBoard(object):
    def __init__(self, device, enable_revelation_leds, is_pi):
        super(DgtBoard, self).__init__()
        self.given_device = device
        self.device = device
        self.enable_revelation_leds = enable_revelation_leds
        self.is_pi = is_pi

        self.serial = None
        self.lock = Lock()  # inside setup_serial_port()
        self.incoming_board_thread = None
        self.lever_pos = None
        # the next three are only used for "not dgtpi" mode
        self.clock_lock = False  # serial connected clock is locked
        self.last_clock_command = []  # Used for resend last (failed) clock command
        self.rt = RepeatedTimer(1, self._watchdog)
        # bluetooth vars for Jessie & autoconnect
        self.btctl = None
        self.bt_rfcomm = None
        self.bt_state = -1
        self.bt_line = ''
        self.bt_current_device = -1
        self.bt_mac_list = []
        self.bt_name_list = []
        self.bt_name = ''
        self.wait_counter = 0
        # keep the old time for finding out errorous DGT_MSG_BWTIME messages (=> new time < old time)
        self.r_time = 3600 * 10  # max value cause 10h cant be reached by clock
        self.l_time = 3600 * 10  # max value cause 10h cant be reached by clock

        self.board_connected_text = None

    def write_board_command(self, message):
        mes = message[3] if message[0].value == DgtCmd.DGT_CLOCK_MESSAGE.value else message[0]
        if not mes == DgtCmd.DGT_RETURN_SERIALNR:
            logging.debug('put (ser) board [%s], length: %i', mes, len(message))
            if mes.value == DgtClk.DGT_CMD_CLOCK_ASCII.value:
                logging.debug('sending text [{}] to (ser) clock'.format(''.join([chr(elem) for elem in message[4:12]])))

        array = []
        char_to_xl = {
            '0': 0x01 | 0x02 | 0x20 | 0x08 | 0x04 | 0x10, '1': 0x02 | 0x04, '2': 0x01 | 0x40 | 0x08 | 0x02 | 0x10,
            '3': 0x01 | 0x40 | 0x08 | 0x02 | 0x04, '4': 0x20 | 0x04 | 0x40 | 0x02,
            '5': 0x01 | 0x40 | 0x08 | 0x20 | 0x04,
            '6': 0x01 | 0x40 | 0x08 | 0x20 | 0x04 | 0x10, '7': 0x02 | 0x04 | 0x01,
            '8': 0x01 | 0x02 | 0x20 | 0x40 | 0x04 | 0x10 | 0x08, '9': 0x01 | 0x40 | 0x08 | 0x02 | 0x04 | 0x20,
            'a': 0x01 | 0x02 | 0x20 | 0x40 | 0x04 | 0x10, 'b': 0x20 | 0x04 | 0x40 | 0x08 | 0x10,
            'c': 0x01 | 0x20 | 0x10 | 0x08,
            'd': 0x10 | 0x40 | 0x08 | 0x02 | 0x04, 'e': 0x01 | 0x40 | 0x08 | 0x20 | 0x10,
            'f': 0x01 | 0x40 | 0x20 | 0x10,
            'g': 0x01 | 0x20 | 0x10 | 0x08 | 0x04, 'h': 0x20 | 0x10 | 0x04 | 0x40, 'i': 0x02 | 0x04,
            'j': 0x02 | 0x04 | 0x08 | 0x10, 'k': 0x01 | 0x20 | 0x40 | 0x04 | 0x10, 'l': 0x20 | 0x10 | 0x08,
            'm': 0x01 | 0x40 | 0x04 | 0x10, 'n': 0x40 | 0x04 | 0x10, 'o': 0x40 | 0x04 | 0x10 | 0x08,
            'p': 0x01 | 0x40 | 0x20 | 0x10 | 0x02, 'q': 0x01 | 0x40 | 0x20 | 0x04 | 0x02, 'r': 0x40 | 0x10,
            's': 0x01 | 0x40 | 0x08 | 0x20 | 0x04, 't': 0x20 | 0x10 | 0x08 | 0x40,
            'u': 0x08 | 0x02 | 0x20 | 0x04 | 0x10,
            'v': 0x08 | 0x02 | 0x20, 'w': 0x40 | 0x08 | 0x20 | 0x02, 'x': 0x20 | 0x10 | 0x04 | 0x40 | 0x02,
            'y': 0x20 | 0x08 | 0x04 | 0x40 | 0x02, 'z': 0x01 | 0x40 | 0x08 | 0x02 | 0x10, ' ': 0x00, '-': 0x40,
            '/': 0x20 | 0x40 | 0x04, '|': 0x20 | 0x10 | 0x02 | 0x04, '\\': 0x02 | 0x40 | 0x10
        }
        for v in message:
            if type(v) is int:
                array.append(v)
            elif isinstance(v, enum.Enum):
                array.append(v.value)
            elif type(v) is str:
                for c in v:
                    array.append(char_to_xl[c.lower()])
            else:
                logging.error('type not supported [%s]', type(v))
                return False

        while True:
            if self.serial:
                try:
                    self.serial.write(bytearray(array))
                    break
                except ValueError:
                    logging.error('invalid bytes sent {0}'.format(message))
                    return False
                except pyserial.SerialException as e:
                    logging.error(e)
                    self.serial.close()
                    self.serial = None
                except IOError as e:
                    logging.error(e)
                    self.serial.close()
                    self.serial = None
            if mes == DgtCmd.DGT_RETURN_SERIALNR:
                break
            time.sleep(0.1)

        if message[0] == DgtCmd.DGT_CLOCK_MESSAGE:
            self.last_clock_command = message
            if self.clock_lock:
                logging.warning('(ser) clock: already locked. Maybe a "resend"?')
            else:
                logging.debug('(ser) clock: now locked')
                self.clock_lock = time.time()
        return True

    def _process_board_message(self, message_id, message, message_length):
        for case in switch(message_id):
            if case(DgtMsg.DGT_MSG_VERSION):
                if message_length != 2:
                    logging.warning('illegal length in data')
                board_version = str(message[0]) + '.' + str(message[1])
                logging.debug("DGT board version %0.2f", float(board_version))
                self.write_board_command([DgtCmd.DGT_SEND_BRD])  # Update the board => get first FEN
                if self.device.find('rfc') == -1:
                    text_l, text_m, text_s = 'USB e-Board', 'USBboard', 'ok usb'
                    channel = 'USB'
                else:
                    btname5 = self.bt_name[-5:]
                    if 'REVII' in self.bt_name:
                        text_l, text_m, text_s = 'RevII ' + btname5, 'Rev' + btname5, 'b' + btname5
                        self.enable_revelation_leds = True
                    elif 'DGT_BT' in self.bt_name:
                        text_l, text_m, text_s = 'DGTBT ' + btname5, 'BT ' + btname5, 'b' + btname5
                        self.enable_revelation_leds = False
                    else:
                        text_l, text_m, text_s = 'BT e-Board', 'BT board', 'ok bt'
                    channel = 'BT'
                self.board_connected_text = Dgt.DISPLAY_TEXT(l=text_l, m=text_m, s=text_s, wait=True, beep=False,
                                                             maxtime=1, devs={'i2c', 'web'})  # serial clock lateron
                DisplayMsg.show(Message.DGT_EBOARD_VERSION(text=self.board_connected_text, channel=channel))
                self.startup_serial_clock()  # now ask the serial clock to answer
                if self.rt.is_running():
                    logging.warning('watchdog timer is already running')
                else:
                    logging.debug('watchdog timer is started')
                    self.rt.start()
                break
            if case(DgtMsg.DGT_MSG_BWTIME):
                if message_length != 7:
                    logging.warning('illegal length in data')
                if ((message[0] & 0x0f) == 0x0a) or ((message[3] & 0x0f) == 0x0a):  # Clock ack message
                    # Construct the ack message
                    ack0 = ((message[1]) & 0x7f) | ((message[3] << 3) & 0x80)
                    ack1 = ((message[2]) & 0x7f) | ((message[3] << 2) & 0x80)
                    ack2 = ((message[4]) & 0x7f) | ((message[0] << 3) & 0x80)
                    ack3 = ((message[5]) & 0x7f) | ((message[0] << 2) & 0x80)
                    if ack0 != 0x10:
                        logging.warning("(ser) clock: ACK error %s", (ack0, ack1, ack2, ack3))
                        if self.last_clock_command:
                            logging.debug('(ser) clock: resending failed message [%s]', self.last_clock_command)
                            self.write_board_command(self.last_clock_command)
                            self.last_clock_command = []  # only resend once
                        break
                    else:
                        logging.debug("(ser) clock: ACK okay [%s]", DgtClk(ack1))
                    if ack1 == 0x88:
                        # this are the other (ack2-ack3) codes
                        # 05-49 33-52 17-51 09-50 65-53 | button 0-4 (single)
                        #       37-52 21-51 13-50 69-53 | button 0 + 1-4
                        #             49-51 41-50 97-53 | button 1 + 2-4
                        #                   25-50 81-53 | button 2 + 3-4
                        #                         73-53 | button 3 + 4
                        if ack3 == 49:
                            logging.info("(ser) clock: button 0 pressed - ack2: %i", ack2)
                            DisplayMsg.show(Message.DGT_BUTTON(button=0, dev='ser'))
                        if ack3 == 52:
                            logging.info("(ser) clock: button 1 pressed - ack2: %i", ack2)
                            DisplayMsg.show(Message.DGT_BUTTON(button=1, dev='ser'))
                        if ack3 == 51:
                            logging.info("(ser) clock: button 2 pressed - ack2: %i", ack2)
                            DisplayMsg.show(Message.DGT_BUTTON(button=2, dev='ser'))
                        if ack3 == 50:
                            logging.info("(ser) clock: button 3 pressed - ack2: %i", ack2)
                            DisplayMsg.show(Message.DGT_BUTTON(button=3, dev='ser'))
                        if ack3 == 53:
                            if ack2 == 69:
                                logging.info("(ser) clock: button 0+4 pressed - ack2: %i", ack2)
                                DisplayMsg.show(Message.DGT_BUTTON(button=0x11, dev='ser'))
                            else:
                                logging.info("(ser) clock: button 4 pressed - ack2: %i", ack2)
                                DisplayMsg.show(Message.DGT_BUTTON(button=4, dev='ser'))
                    if ack1 == 0x09:
                        main = ack2 >> 4
                        sub = ack2 & 0x0f
                        logging.debug("(ser) clock: version %0.2f", float(str(main) + '.' + str(sub)))
                        if self.board_connected_text:
                            self.board_connected_text.devs = {'ser'}  # Now send the (delayed) message to serial clock
                            dev = 'ser'
                        else:
                            dev = 'err'
                        DisplayMsg.show(Message.DGT_CLOCK_VERSION(main=main, sub=sub, dev=dev, text=self.board_connected_text))
                    if ack1 == 0x0a:  # clock ack SETNRUN => set the time values to max for sure! override lateron
                        self.r_time = 3600 * 10
                        self.l_time = 3600 * 10
                elif any(message[:6]):
                    r_hours = message[0] & 0x0f
                    r_mins = (message[1] >> 4) * 10 + (message[1] & 0x0f)
                    r_secs = (message[2] >> 4) * 10 + (message[2] & 0x0f)
                    l_hours = message[3] & 0x0f
                    l_mins = (message[4] >> 4) * 10 + (message[4] & 0x0f)
                    l_secs = (message[5] >> 4) * 10 + (message[5] & 0x0f)
                    r_time = r_hours * 3600 + r_mins * 60 + r_secs
                    l_time = l_hours * 3600 + l_mins * 60 + l_secs
                    errtim = r_hours > 9 or l_hours > 9 or r_mins > 59 or l_mins > 59 or r_secs > 59 or l_secs > 59
                    if errtim:  # complete illegal package received
                        logging.warning('(ser) clock: illegal time received {}'.format(message))
                    elif r_time > self.r_time or l_time > self.l_time:  # the new time is higher as the old => ignore
                        logging.warning('(ser) clock: strange time received {} l:{} r:{}'.format(
                            message, hours_minutes_seconds(self.l_time), hours_minutes_seconds(self.r_time)))
                    else:
                        status = (message[6] & 0x3f)
                        if status & 0x20:
                            logging.warning('(ser) clock: not connected')
                            self.lever_pos = None
                        else:
                            tr = [r_hours, r_mins, r_secs]
                            tl = [l_hours, l_mins, l_secs]
                            logging.info('(ser) clock: received time from clock l:{} r:{}'.format(tl, tr))
                            DisplayMsg.show(Message.DGT_CLOCK_TIME(time_left=tl, time_right=tr, dev='ser'))

                            right_side_down = -0x40 if status & 0x02 else 0x40
                            if self.lever_pos != right_side_down:
                                logging.debug('(ser) clock: button status: {} old lever_pos: {}'.format(
                                    status, self.lever_pos))
                                if self.lever_pos is not None:
                                    DisplayMsg.show(Message.DGT_BUTTON(button=right_side_down, dev='ser'))
                                self.lever_pos = right_side_down
                    if not errtim:
                        self.r_time = r_time
                        self.l_time = l_time
                else:
                    logging.debug('(ser) clock: null message ignored')
                if self.clock_lock:
                    logging.debug('(ser) clock: unlocked after {0:.3f} secs'.format(time.time() - self.clock_lock))
                    self.clock_lock = False
                break
            if case(DgtMsg.DGT_MSG_BOARD_DUMP):
                if message_length != 64:
                    logging.warning('illegal length in data')
                piece_to_char = {
                    0x01: 'P', 0x02: 'R', 0x03: 'N', 0x04: 'B', 0x05: 'K', 0x06: 'Q',
                    0x07: 'p', 0x08: 'r', 0x09: 'n', 0x0a: 'b', 0x0b: 'k', 0x0c: 'q',
                    0x0d: '1', 0x0e: '2', 0x0f: '3', 0x00: '.'
                }
                board = ''
                for c in message:
                    board += piece_to_char[c]
                logging.debug('\n' + '\n'.join(board[0 + i:8 + i] for i in range(0, len(board), 8)))  # Show debug board
                # Create fen from board
                fen = ''
                empty = 0
                for sq in range(0, 64):
                    if message[sq] != 0:
                        if empty > 0:
                            fen += str(empty)
                            empty = 0
                        fen += piece_to_char[message[sq]]
                    else:
                        empty += 1
                    if (sq + 1) % 8 == 0:
                        if empty > 0:
                            fen += str(empty)
                            empty = 0
                        if sq < 63:
                            fen += '/'

                # Attention! This fen is NOT flipped
                logging.debug("Raw-Fen [%s]", fen)
                DisplayMsg.show(Message.DGT_FEN(fen=fen))
                break
            if case(DgtMsg.DGT_MSG_FIELD_UPDATE):
                if message_length != 2:
                    logging.warning('illegal length in data')
                self.write_board_command([DgtCmd.DGT_SEND_BRD])  # Ask for the board when a piece moved
                break
            if case(DgtMsg.DGT_MSG_SERIALNR):
                if message_length != 5:
                    logging.warning('illegal length in data')
                DisplayMsg.show(Message.DGT_SERIAL_NR(number=''.join([chr(elem) for elem in message])))
                break
            if case(DgtMsg.DGT_MSG_BATTERY_STATUS):
                if message_length != 9:
                    logging.warning('illegal length in data')
                logging.debug(message)
                break
            if case():  # Default
                logging.warning("DGT message not handled [%s]", DgtMsg(message_id))

    def _read_board_message(self, head):
        message = ()
        header_len = 3
        header = head + self.serial.read(header_len - 1)
        try:
            header = struct.unpack('>BBB', header)
        except struct.error:
            logging.warning('timeout in header reading')
            return message
        message_id = header[0]
        message_length = counter = (header[1] << 7) + header[2] - header_len
        if message_length <= 0 or message_length > 64:
            logging.warning("illegal length in message header %i length: %i", message_id, message_length)
            return message

        try:
            if not message_id == DgtMsg.DGT_MSG_SERIALNR:
                logging.debug("get (ser) board [%s], length: %i", DgtMsg(message_id), message_length)
        except ValueError:
            logging.warning("illegal id in message header %i length: %i", message_id, message_length)
            return message

        while counter:
            byte = self.serial.read(1)
            if byte:
                data = struct.unpack('>B', byte)
                counter -= 1
                if data[0] & 0x80:
                    logging.warning('illegal data in message %i found', message_id)
                    logging.warning('ignore collected message data %s', format(message))
                    return self._read_board_message(byte)
                message += data
            else:
                logging.warning('timeout in data reading')

        self._process_board_message(message_id, message, message_length)
        return message

    def _process_incoming_board_forever(self):
        counter = 0
        logging.info('incoming_board ready')
        while True:
            try:
                byte = None
                if self.serial:
                    byte = self.serial.read(1)
                else:
                    self._setup_serial_port()
                    if self.serial:
                        logging.debug('sleeping for 1.5 secs. Afterwards startup the (ser) hardware')
                        time.sleep(1.5)
                        self._startup_serial_board()
                if byte and byte[0] & 0x80:
                    self._read_board_message(head=byte)
                else:
                    counter = (counter + 1) % 10
                    if counter == 0:  # issue 150 - check for alive connection
                        self._watchdog()  # force to write something to the board
                    time.sleep(0.1)
            except pyserial.SerialException:
                pass
            except TypeError:
                pass
            except struct.error:  # can happen, when plugin board-cable again
                pass

    def startup_serial_clock(self):
        self.clock_lock = False
        command = [DgtCmd.DGT_CLOCK_MESSAGE, 0x03, DgtClk.DGT_CMD_CLOCK_START_MESSAGE,
                   DgtClk.DGT_CMD_CLOCK_VERSION, DgtClk.DGT_CMD_CLOCK_END_MESSAGE]
        self.write_board_command(command)  # Get clock version

    def _startup_serial_board(self):
        self.write_board_command([DgtCmd.DGT_SEND_UPDATE_NICE])  # Set the board update mode
        self.write_board_command([DgtCmd.DGT_SEND_VERSION])  # Get board version

    def _watchdog(self):
        self.write_board_command([DgtCmd.DGT_RETURN_SERIALNR])

    def _open_bluetooth(self):
        if self.bt_state == -1:
            # only for jessie
            if path.exists("/usr/bin/bluetoothctl"):
                self.bt_state = 0

                # get rid of old rfcomm
                if path.exists("/dev/rfcomm123"):
                    logging.debug('BT releasing /dev/rfcomm123')
                    subprocess.call(["rfcomm", "release", "123"])
                self.bt_current_device = -1
                self.bt_mac_list = []
                self.bt_name_list = []

                logging.debug("BT starting bluetoothctl")
                self.btctl = subprocess.Popen("/usr/bin/bluetoothctl",
                                              stdin=subprocess.PIPE,
                                              stdout=subprocess.PIPE,
                                              stderr=subprocess.STDOUT,
                                              universal_newlines=True,
                                              shell=True)

                # set the O_NONBLOCK flag of file descriptor:
                flags = fcntl(self.btctl.stdout, F_GETFL)  # get current flags
                fcntl(self.btctl.stdout, F_SETFL, flags | O_NONBLOCK)

                self.btctl.stdin.write("power on\n")
                self.btctl.stdin.flush()
        else:
            # state >= 0 so bluetoothctl is running

            # check for new data from bluetoothctl
            try:
                while True:
                    b = read(self.btctl.stdout.fileno(), 1).decode(encoding='UTF-8', errors='ignore')
                    self.bt_line += b
                    if b == '' or b == '\n':
                        break
            except OSError:
                time.sleep(0.1)

            # complete line
            if '\n' in self.bt_line:
                if "Changing power on succeeded" in self.bt_line:
                    self.bt_state = 1
                    self.btctl.stdin.write("agent on\n")
                    self.btctl.stdin.flush()
                if "Agent registered" in self.bt_line:
                    self.bt_state = 2
                    self.btctl.stdin.write("default-agent\n")
                    self.btctl.stdin.flush()
                if "Default agent request successful" in self.bt_line:
                    self.bt_state = 3
                    self.btctl.stdin.write("scan on\n")
                    self.btctl.stdin.flush()
                if "Discovering: yes" in self.bt_line:
                    self.bt_state = 4
                if "Pairing successful" in self.bt_line:
                    self.bt_state = 6
                    logging.debug("BT pairing successful")
                if "Failed to pair: org.bluez.Error.AlreadyExists" in self.bt_line:
                    self.bt_state = 6
                    logging.debug("BT already paired")
                elif "Failed to pair" in self.bt_line:
                    # try the next
                    self.bt_state = 4
                    logging.debug("BT pairing failed")
                if "not available" in self.bt_line:
                    # remove and try the next
                    self.bt_state = 4
                    self.bt_mac_list.remove(self.bt_mac_list[self.bt_current_device])
                    self.bt_name_list.remove(self.bt_name_list[self.bt_current_device])
                    self.bt_current_device -= 1
                    logging.debug("BT pairing failed, unknown device")
                if ("DGT_BT_" in self.bt_line or "PCS-REVII" in self.bt_line) and "DEL" not in self.bt_line:
                    # New e-Board found add to list
                    try:
                        if not self.bt_line.split()[3] in self.bt_mac_list:
                            self.bt_mac_list.append(self.bt_line.split()[3])
                            self.bt_name_list.append(self.bt_line.split()[4])
                            logging.debug('BT found device: %s %s', self.bt_line.split()[3], self.bt_line.split()[4])
                    except IndexError:
                        logging.error('wrong bt_line [%s]', self.bt_line)

                # clear the line
                self.bt_line = ''

            if "Enter PIN code:" in self.bt_line:
                if "DGT_BT_" in self.bt_name_list[self.bt_current_device]:
                    self.btctl.stdin.write("0000\n")
                    self.btctl.stdin.flush()
                if "PCS-REVII" in self.bt_name_list[self.bt_current_device]:
                    self.btctl.stdin.write("1234\n")
                    self.btctl.stdin.flush()
                self.bt_line = ''

            if "Confirm passkey" in self.bt_line:
                self.btctl.stdin.write("yes\n")
                self.btctl.stdin.flush()
                self.bt_line = ''

            # if there are devices in the list try one
            if self.bt_state == 4:
                if len(self.bt_mac_list) > 0:
                    self.bt_state = 5
                    self.bt_current_device += 1
                    if self.bt_current_device >= len(self.bt_mac_list):
                        self.bt_current_device = 0
                    logging.debug("BT pairing to: %s %s",
                                  self.bt_mac_list[self.bt_current_device],
                                  self.bt_name_list[self.bt_current_device])
                    self.btctl.stdin.write("pair " + self.bt_mac_list[self.bt_current_device] + "\n")
                    self.btctl.stdin.flush()

            # pair succesful, try rfcomm
            if self.bt_state == 6:
                # now try rfcomm
                self.bt_state = 7
                self.bt_rfcomm = subprocess.Popen("rfcomm connect 123 " + self.bt_mac_list[self.bt_current_device],
                                                  stdin=subprocess.PIPE,
                                                  stdout=subprocess.PIPE,
                                                  stderr=subprocess.PIPE,
                                                  universal_newlines=True,
                                                  shell=True)

            # wait for rfcomm to fail or suceed
            if self.bt_state == 7:
                # rfcomm succeeded
                if path.exists("/dev/rfcomm123"):
                    logging.debug("BT connected to: %s", self.bt_name_list[self.bt_current_device])
                    if self._open_serial("/dev/rfcomm123"):
                        self.btctl.stdin.write("quit\n")
                        self.btctl.stdin.flush()
                        self.bt_name = self.bt_name_list[self.bt_current_device]

                        self.bt_state = -1
                        return True
                # rfcomm failed
                if self.bt_rfcomm.poll() is not None:
                    logging.debug("BT rfcomm failed")
                    self.btctl.stdin.write("remove " + self.bt_mac_list[self.bt_current_device] + "\n")
                    self.bt_mac_list.remove(self.bt_mac_list[self.bt_current_device])
                    self.bt_name_list.remove(self.bt_name_list[self.bt_current_device])
                    self.bt_current_device -= 1
                    self.btctl.stdin.flush()
                    self.bt_state = 4
        return False

    def _open_serial(self, device):
        try:
            self.serial = pyserial.Serial(device, stopbits=pyserial.STOPBITS_ONE,
                                          parity=pyserial.PARITY_NONE, bytesize=pyserial.EIGHTBITS, timeout=2)
        except pyserial.SerialException:
            return False
        return True

    def _setup_serial_port(self):
        def success(dev):
            self.device = dev
            logging.debug('DGT board connected to %s', self.device)
            return True

        waitchars = ['/', '-', '\\', '|']

        if self.rt.is_running():
            logging.debug('watchdog timer is stopped now')
            self.rt.stop()
        with self.lock:
            if not self.serial:
                if self.given_device:
                    if self._open_serial(self.given_device):
                        return success(self.given_device)
                else:
                    for file in os.listdir('/dev'):
                        if file.startswith('ttyACM') or file.startswith('ttyUSB') or file == 'rfcomm0':
                            dev = os.path.join('/dev', file)
                            if self._open_serial(dev):
                                return success(dev)
                    if self._open_bluetooth():
                        return success('/dev/rfcomm123')

                # text = self.dgttranslate.text('N00_noboard', 'Board' + waitchars[self.wait_counter])
                s = 'Board' + waitchars[self.wait_counter]
                text = Dgt.DISPLAY_TEXT(l='no e-' + s, m='no' + s, s=s,
                                        wait=True, beep=False, maxtime=0, devs={'i2c', 'web'})
                DisplayMsg.show(Message.DGT_NO_EBOARD_ERROR(text=text))
                self.wait_counter = (self.wait_counter + 1) % len(waitchars)
        return False

    def _wait_for_clock(self):
        has_to_wait = False
        counter = 0
        while self.clock_lock:
            if not has_to_wait:
                has_to_wait = True
                logging.debug('(ser) clock is locked => waiting')
            time.sleep(0.1)
            counter += 1
            if counter > 20:
                logging.warning('(ser) clock is locked over 2secs')
                logging.debug('resending locked (ser) clock message [%s]', self.last_clock_command)
                has_to_wait = False
                counter = 0
                self.write_board_command(self.last_clock_command)
        if has_to_wait:
            logging.debug('(ser) clock is released now')

    def set_text_3k(self, text, beep, left_icons=ClockIcons.NONE, right_icons=ClockIcons.NONE):
        self._wait_for_clock()
        res = self.write_board_command([DgtCmd.DGT_CLOCK_MESSAGE, 0x0c, DgtClk.DGT_CMD_CLOCK_START_MESSAGE,
                                        DgtClk.DGT_CMD_CLOCK_ASCII,
                                        text[0], text[1], text[2], text[3], text[4], text[5], text[6], text[7], beep,
                                        DgtClk.DGT_CMD_CLOCK_END_MESSAGE])
        return res

    def set_text_xl(self, text, beep, left_icons=ClockIcons.NONE, right_icons=ClockIcons.NONE):
        def transfer(icons):
            result = 0
            if icons == ClockIcons.DOT:
                result = 0x01
            if icons == ClockIcons.COLON:
                result = 0x02
            return result

        self._wait_for_clock()
        icn = ((transfer(right_icons) & 0x07) | (transfer(left_icons) << 3) & 0x38)
        res = self.write_board_command([DgtCmd.DGT_CLOCK_MESSAGE, 0x0b, DgtClk.DGT_CMD_CLOCK_START_MESSAGE,
                                        DgtClk.DGT_CMD_CLOCK_DISPLAY,
                                        text[2], text[1], text[0], text[5], text[4], text[3], icn, beep,
                                        DgtClk.DGT_CMD_CLOCK_END_MESSAGE])
        return res

    def set_and_run(self, lr, lh, lm, ls, rr, rh, rm, rs):
        self._wait_for_clock()
        side = ClockSide.NONE
        if lr == 1 and rr == 0:
            side = ClockSide.LEFT
        if lr == 0 and rr == 1:
            side = ClockSide.RIGHT
        res = self.write_board_command([DgtCmd.DGT_CLOCK_MESSAGE, 0x0a, DgtClk.DGT_CMD_CLOCK_START_MESSAGE,
                                        DgtClk.DGT_CMD_CLOCK_SETNRUN,
                                        lh, lm, ls, rh, rm, rs, side,
                                        DgtClk.DGT_CMD_CLOCK_END_MESSAGE])
        return res

    def end_text(self):
        self._wait_for_clock()
        res = self.write_board_command([DgtCmd.DGT_CLOCK_MESSAGE, 0x03, DgtClk.DGT_CMD_CLOCK_START_MESSAGE,
                                        DgtClk.DGT_CMD_CLOCK_END,
                                        DgtClk.DGT_CMD_CLOCK_END_MESSAGE])
        return res

    def run(self):
        self.incoming_board_thread = Timer(0, self._process_incoming_board_forever)
        self.incoming_board_thread.start()
