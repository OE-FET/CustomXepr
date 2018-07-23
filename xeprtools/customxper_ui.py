# -*- coding: utf-8 -*-

"""
Created on Tue Aug 23 11:03:57 2016

@author: Sam Schott  (ss2151@cam.ac.uk)

ockpot(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""

# system module imports
import sys
import os
from qtpy import QtCore, QtWidgets, QtGui, uic
import logging
import logging.handlers
import platform
import subprocess
import re
import pydoc

# custom module imports
from keithley_driver import SweepData
from xeprtools import customxepr
from xeprtools.mode_picture import ModePicture
from utils import dark_style
from config.main import CONF

logger = logging.getLogger(__name__)
root_logger = logging.getLogger()
# log all messages with level STATUS and higher (no DEBUG messages)
logger.setLevel(logging.STATUS)


def linux_notify(**kwargs):
    """Small script to send linux pop-up notifications."""
    if str(sys.platform) == 'linux2':
        subprocess.call(["notify-send",
                         kwargs.get('title'),
                         kwargs.get('message')])


class QInfoLogHandler(logging.Handler, QtCore.QObject):
    """
    Handler which adds all logging event messages to a QStandardItemModel. This
    model will be used to populate the "Message log" in the GUI with all
    logging messages of level INFO and higher.
    """
    def __init__(self):
        super(self.__class__, self).__init__()
        # create QStandardItemModel
        self.model = QtGui.QStandardItemModel(0, 3)
        self.model.setHorizontalHeaderLabels(['Time', 'Level', 'Message'])

    def emit(self, record):
        # format logging record
        self.format(record)
        time_item = QtGui.QStandardItem(record.asctime)
        level_item = QtGui.QStandardItem(record.levelname)
        msg_item = QtGui.QStandardItem(record.msg)
        # add logging record to QStandardItemModel
        self.model.appendRow([time_item, level_item, msg_item])
        # show small pop up notification in linux
        linux_notify(title='CustomXepr info', message=record.message)


class QStatusLogHandler(logging.Handler, QtCore.QObject):
    """
    Handler which emits a signal containing the logging message for every
    logged event. The signal will be connected to "Status" field of the GUI and
    trigger a 30 min timout counter which will be reset by the next signal.
    """
    status_signal = QtCore.Signal(str)

    def __init__(self):
        logging.Handler.__init__(self)
        QtCore.QObject.__init__(self)

    def emit(self, record):
        # format logging record
        self.format(record)
        # emit logging record as signal
        self.status_signal.emit('Status: ' + record.message)


# =========================================================================
# Set up handlers for STATUS and INFO messages
# =========================================================================

# create QInfoLogHandler to handle all INFO level events
info_fmt = logging.Formatter(fmt='%(asctime)s %(threadName)s ' +
                             '%(levelname)s: %(message)s', datefmt='%H:%M')
info_handler = QInfoLogHandler()
info_handler.setFormatter(info_fmt)
info_handler.setLevel(logging.INFO)
root_logger.addHandler(info_handler)

# create QStatusLogHandler to handle all STATUS level events
status_handler = QStatusLogHandler()
status_handler.setLevel(logging.STATUS)
root_logger.addHandler(status_handler)


# =============================================================================
# Define JobStatusApp class
# =============================================================================

class JobStatusApp(QtWidgets.QMainWindow):
    def __init__(self, customXepr):
        """
        Gives an overview of all loggig messages and pending jobs in a
        job_queue. The worker thread can be pause/resumed and all pending jobs
        can be cancelled and by pressing the respective buttons.

        A side panael allows the user to adjust some basic settings for email
        notifications, the temperature control, etc.

        This class requires a CustomXepr instance as input.

        """
        super(self.__class__, self).__init__()
        # load user interface layout from .ui file
        uic.loadUi(os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                'job_window.ui'), self)
        # get input arguments
        self.customXepr = customXepr
        self.job_queue = customXepr.job_queue
        self.result_queue = customXepr.result_queue
        self.pause_event = customXepr.pause_event
        self.abort_event = customXepr.abort_event
        self.abort_event_keithley = customXepr.keithley.abort_event

        # create about window
        self.aboutWindow = AboutWindow()

        # assign menu bar actions
        self.action_About.triggered.connect(self.aboutWindow.show)
        self.actionShow_log_files.triggered.connect(self.on_log_clicked)
        self.action_Exit.triggered.connect(self.exit_)
        self.actionDark_mode.triggered.connect(self._toggle_dark_mode)

        if CONF.get('main', 'DARK'):
            self.actionDark_mode.setChecked(True)
        else:
            self.actionDark_mode.setChecked(False)

        # position in top left corner
        self.setIntialPosition()

        # =====================================================================
        # Update user interface to reflect current status of CustomXepr
        # =====================================================================

        # check status of worker thread (Paused or Running) and adjust buttons
        if self.pause_event.is_set():
            self.pauseButton.setText('Resume')
        elif not self.pause_event.is_set():
            self.pauseButton.setText('Pause')

        # get email list and notification level
        self.getEmailList()
        self.getNotificationLevel()

        # get temperature control settings
        self.lineEditT_tolerance.setText(str(self.customXepr.temperature_tolerance))
        self.lineEditT_settling.setText(str(self.customXepr.temp_wait_time))

        # perform various UI updates after status change
        status_handler.status_signal.connect(self.statusField.setText)
        status_handler.status_signal.connect(self.checkPaused)
        status_handler.status_signal.connect(self.getEmailList)

        # create data models for message log, job queue and result queue
        self.messageLogModel = info_handler.model
        self.jobQueueModel = QtGui.QStandardItemModel(0, 3)
        self.resultQueueModel = QtGui.QStandardItemModel(0, 3)

        h1 = ['Time', 'Level', 'Message']
        h2 = ['Function', 'Arguments', 'Keywoard Arguments']
        h3 = ['Type', 'Size', 'Value']

        self.messageLogModel.setHorizontalHeaderLabels(h1)
        self.jobQueueModel.setHorizontalHeaderLabels(h2)
        self.resultQueueModel.setHorizontalHeaderLabels(h3)

        # add models to views
        self.messageLogDisplay.setModel(self.messageLogModel)
        self.jobQueueDisplay.setModel(self.jobQueueModel)
        self.resultQueueDisplay.setModel(self.resultQueueModel)

        # populate models with queue elemts
        self.populateResults()
        self.populateJobs()

        # update views when items are added to or removed from queues
        self.customXepr.result_queue.pop_signal.connect(self.removeResult)
        self.customXepr.result_queue.put_signal.connect(self.addResult)

        self.customXepr.job_queue.pop_signal.connect(self.removeJob)
        self.customXepr.job_queue.put_signal.connect(self.addJob)

        # set context menues for job_queue and result_queue items
        self.resultQueueDisplay.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.jobQueueDisplay.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

        self.resultQueueDisplay.customContextMenuRequested.connect(self.openResultContextMenu)
        self.jobQueueDisplay.customContextMenuRequested.connect(self.openJobContextMenu)

        # =====================================================================
        # Connect signals, slots, and callbacks
        # =====================================================================
        self.tuneButton.clicked.connect(self.on_tune_clicked)
        self.qValueButton.clicked.connect(self.on_qValue_clicked)

        self.pauseButton.clicked.connect(self.on_pause_clicked)
        self.abortButton.clicked.connect(self.on_abort_clicked)
        self.clearButton.clicked.connect(self.on_clear_clicked)
        self.pushButtonLogFiles.clicked.connect(self.on_log_clicked)

        # accept only numbers as input of temperature tolerance
        # and settling time
        self.lineEditT_tolerance.setValidator(QtGui.QDoubleValidator())
        self.lineEditT_settling.setValidator(QtGui.QDoubleValidator())

        self.lineEditEmailList.returnPressed.connect(self.setEmailList)
        self.lineEditT_tolerance.returnPressed.connect(self.set_temperature_tolerance)
        self.lineEditT_settling.returnPressed.connect(self.setT_settling)

        self.bG = QtWidgets.QButtonGroup(self)
        self.bG.setExclusive(True)
        self.bG.addButton(self.radioButtonErrorMail)
        self.bG.addButton(self.radioButtonWarningMail)
        self.bG.addButton(self.radioButtonInfoMail)
        self.bG.addButton(self.radioButtonNoMail)

        self.bG.buttonClicked['int'].connect(self.onbGClicked)

        # Universal timeout:
        # Send an email notification if there is no status update for 30 min.

        _timeout_min = 30  # time in minutes before timeout warning
        self.min2msec = 60.0*1000.0  # conversion factor for min to msec

        self.timeout_timer = QtCore.QTimer()
        self.timeout_timer.setInterval(_timeout_min * self.min2msec)
        self.timeout_timer.setSingleShot(True)
        self.timeout_timer.timeout.connect(self.timeout_warning)

        status_handler.status_signal.connect(self.timeout_timer.start)
# =============================================================================
# User interface setup
# =============================================================================

    def setIntialPosition(self):
        screen = QtWidgets.QDesktopWidget().screenGeometry(self)

        xPos = screen.left()
        yPos = screen.top()
        width = screen.width()/2
        height = screen.height()*2/3

        self.setGeometry(xPos, yPos, width, height)

        self.splitter.setSizes([width*0.7, width*0.3])

    def openResultContextMenu(self):
        """
        Context menu for items in resultQueueDisplay. Gives the options to
        delete the item, to plot it or to save it, depending on type.
        """
        PLOT_RESULT = ['SweepData', 'ModePicture']
        SAVE_RESULT = ['SweepData', 'ModePicture']

        indexes = self.resultQueueDisplay.selectedIndexes()
        if indexes == []:
            return

        i0, i1 = indexes[0].row(), indexes[-1].row()

        self.popup_menu = QtWidgets.QMenu()

        deleteAction = self.popup_menu.addAction('Delete entry')
        plotAction = None
        saveAction = None

        if self.resultQueueModel.data(indexes[0]) in PLOT_RESULT and i0 == i1:
            plotAction = self.popup_menu.addAction('Plot data')
        if self.resultQueueModel.data(indexes[0]) in SAVE_RESULT and i0 == i1:
            saveAction = self.popup_menu.addAction('Save data')

        action = self.popup_menu.exec_(QtGui.QCursor.pos())

        if action == 0:
            return

        elif action == deleteAction:
            for i in range(i1, i0-1, -1):
                with self.result_queue.mutex:
                    del self.result_queue.queue[i]
                self.resultQueueModel.removeRow(i)

        elif action == saveAction:
            self.result_queue.queue[i0].save()

        elif action == plotAction:
            self.result_queue.queue[i0].plot()

    def openJobContextMenu(self):
        """
        Context menu for items in jobQueueDisplay. Gives the option to
        delete the item.
        """
        indexes = self.jobQueueDisplay.selectedIndexes()
        i0, i1 = indexes[0].row(), indexes[-1].row()

        self.popup_menu = QtWidgets.QMenu()

        deleteAction = self.popup_menu.addAction('Delete entry')
        action = self.popup_menu.exec_(QtGui.QCursor.pos())

        if action == deleteAction:
            for i in range(i1, i0-1, -1):
                with self.job_queue.mutex:
                    del self.job_queue.queue[i]
                self.job_queue.task_done()
                self.jobQueueModel.removeRow(i)

    def exit_(self):
        self.on_abort_clicked()
        self.on_clear_clicked()

        self.deleteLater()

    def timeout_warning(self):
        """
        Issues a warning email if no status update has come in for the time
        specified in self.t_timeout.
        """
        if self.job_queue.unfinished_tasks > 0:
            logger.warning('No status update for %s min.' % self.t_timeout +
                           ' Please check on experiment')

# =============================================================================
# Functions to handle communication with job and result queues
# =============================================================================
    def addJob(self, index=-1):
        """
        Adds new entry to jobQueueDisplay.
        """
        func, args, kwargs = self.job_queue.queue[index]

        if args[0] == self.customXepr:
            args = args[1:]

        func_str = QtGui.QStandardItem(func.func_name)
        args_str = QtGui.QStandardItem(str(args))
        kwargs_str = QtGui.QStandardItem(str(kwargs))

        self.jobQueueModel.appendRow([func_str, args_str, kwargs_str])

    def removeJob(self, index=0):
        """
        Removes top entry of jobQueueDisplay.
        """
        self.jobQueueModel.removeRow(index)

    def addResult(self, index=-1):
        """
        Adds new result to resultQueueDisplay
        """
        result = self.result_queue.queue[index]

        try:
            result_size = len(result)
        except TypeError:
            result_size = '--'

        if type(result) == SweepData:
            result.plot()
        elif type(result) == ModePicture:
            result.plot()

        rslt_type = QtGui.QStandardItem(type(result).__name__)
        rslt_size = QtGui.QStandardItem(str(result_size))
        rslt_value = QtGui.QStandardItem(str(result))

        self.resultQueueModel.appendRow([rslt_type, rslt_size, rslt_value])

    def removeResult(self, index=0):
        """
        Removes top entry of resultQueueDisplay.
        """
        self.resultQueueModel.removeRow(index)

    def populateResults(self):
        """
        Gets all current items of result_queue and adds them to
        resultQueueDisplay.
        """
        for i in range(0, self.result_queue.qsize()):
            self.addResult(i)

    def populateJobs(self):
        """
        Gets all current items of job_queue and adds them to jobQueueDisplay.
        """
        for i in range(0, self.job_queue.qsize()):
            self.addJob(i)

    def checkPaused(self):
        """
        Checks if worker thread is running and updates the Run/Pause button
        accordingly.
        """
        # check status of worker thread
        if self.pause_event.is_set():
            self.pauseButton.setText('Resume')
        elif not self.pause_event.is_set():
            self.pauseButton.setText('Pause')

# =============================================================================
# Button callbacks: Pause / Resume, Clear, Abort, Show Log Files, Tune, QValue
# =============================================================================

    def on_tune_clicked(self):
        """ Schedules a tuning job if the ESR is connected."""

        self.customXepr.customtune()

        if self.job_queue.qsize() > 1:
            logger.info('Tuning job added to the end of the job queue.')

    def on_qValue_clicked(self):
        """ Schedules a Q-Value readout if the ESR is connected."""

        self.customXepr.getQValueCalc()

        if self.job_queue.qsize() > 1:
            logger.info('Q-Value readout added to the end of the job queue.')

    def on_clear_clicked(self):
        """ Clears all pending jobs in job_queue."""
        for item in range(0, self.job_queue.qsize()):
            self.job_queue.get()
            self.job_queue.task_done()

    def on_pause_clicked(self):
        """
        Pauses or resumes worker thread on button click.
        """
        if not self.pause_event.is_set():
            self.pause_event.set()
            self.pauseButton.setText('Resume')

        elif self.pause_event.is_set():
            self.pause_event.clear()
            self.pauseButton.setText('Pause')
            logger.status('IDLE')

    def on_abort_clicked(self):
        """
        Aborts a running job.
        """
        if self.job_queue.unfinished_tasks > 0:
            self.abort_event.set()
            self.abort_event_keithley.set()

    def on_log_clicked(self):
        """
        Opens directory with log files with current log file selected.
        """
        path = self.customXepr.log_file_dir

        if platform.system() == 'Windows':
            os.startfile(path)
        elif platform.system() == 'Darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])

    def _toggle_dark_mode(self):
        """ Toggles between bright and dark GUI appearance."""
        if self.actionDark_mode.isChecked():
            dark_style.go_dark()
            dark_style.apply_mpl_dark_theme()

            CONF.set('main', 'DARK', True)
        elif not self.actionDark_mode.isChecked():
            dark_style.go_bright()
            dark_style.apply_mpl_bright_theme()

            CONF.set('main', 'DARK', False)

# =============================================================================
# Callbacks and functions for CustomXepr settings adjustments
# =============================================================================

    def setEmailList(self):
        """
        Gets the email list from user interface and udates it in CustomXepr.
        """
        # get string from lineEdit field
        adressString = self.lineEditEmailList.text()
        # convert string to list of strings
        adressList = adressString.split(',')
        # strip trailing spaces
        adressList = [x.strip() for x in adressList]
        # validate correct email address format
        for email in adressList:
            if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
                logger.info(email + ' is not a valid email address.')
                adressList = [x for x in adressList if (x is not email)]

        # send list to CustomXepr
        self.customXepr.notify_address = adressList

    def getEmailList(self):
        """
        Gets the email list from CustomXepr and udates it in the GUI.
        """
        adressList = self.customXepr.notify_address
        if not self.lineEditEmailList.hasFocus():
            self.lineEditEmailList.setText(', '.join(adressList))

    def getNotificationLevel(self):
        """
        Checks the notification level for email handler and sets the respective
        checkButton to checked.
        """
        level = self.customXepr.email_handler_level
        if level == 40:
            self.radioButtonErrorMail.setChecked(True)
        elif level == 30:
            self.radioButtonWarningMail.setChecked(True)
        elif level == 20:
            self.radioButtonInfoMail.setChecked(True)
        elif level == 50:
            self.radioButtonNoMail.setChecked(True)

    def onbGClicked(self):
        """ Sets the email notification level to the selected level."""
        clickedButton = self.bG.checkedButton()
        if clickedButton == self.radioButtonErrorMail:
            self.customXepr.email_handler_level = 40
        elif clickedButton == self.radioButtonWarningMail:
            self.customXepr.email_handler_level = 30
        elif clickedButton == self.radioButtonInfoMail:
            self.customXepr.email_handler_level = 20
        elif clickedButton == self.radioButtonNoMail:
            self.customXepr.email_handler_level = 50

    def set_temperature_tolerance(self):
        newString = self.lineEditT_tolerance.text()
        self.customXepr.temperature_tolerance = float(newString)

    def setT_settling(self):
        newString = self.lineEditT_settling.text()
        self.customXepr.temp_wait_time = float(newString)

# =============================================================================
# Properties
# =============================================================================

    @property
    def t_timeout(self):
        """ Gets the timeout limit in minutes from timeout_timer."""
        return self.timeout_timer.interval()/self.min2msec

    @t_timeout.setter
    def t_timeout(self, time_in_min):
        """ Sets the timeout limit in minutes in timeout_timer."""
        self.timeout_timer.setInterval(time_in_min * self.min2msec)


class AboutWindow(QtWidgets.QWidget, QtCore.QCoreApplication):
    """
    Prints version number, copyright info and help output from CustomXepr to a
    PyQt window.
    """
    def __init__(self):

        super(self.__class__, self).__init__()
        # load user interface file
        uic.loadUi(os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                'about_window.ui'), self)
        # get help output in plain text format
        self.help_output = pydoc.plain(pydoc.render_doc(customxepr.CustomXepr))
        # print help output to scroll area of window
        self.docText.setText(self.help_output)
        # set title string of window to CustomXepr version
        self.title_string = customxepr.__name__ + ' ' + customxepr.__version__
        self.titleText.setText(self.title_string)