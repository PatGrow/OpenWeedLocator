from logger import Logger
from threading import Thread, Condition
import collections
import time

# check if platform being used is laptop without GPIO functionality
from sys import platform
if platform == "win32":
    testing = True
else:
    #print(platform)
    from gpiozero import Buzzer, OutputDevice
    testing = False

# two test classes to run the analysis on a desktop computer if a "win32" platform is detected
class TestRelay:
    def __init__(self, relayNumber):
        self.relayNumber = relayNumber

    def on(self):
        print("[TEST] Relay {} ON".format(self.relayNumber))

    def off(self):
        print("[TEST] Relay {} OFF".format(self.relayNumber))

class TestBuzzer:
    def beep(self, on_time: int, off_time: int, n=1):
        for i in range(n):
            print('BEEP')

# control class for the relay board
class RelayControl:
    def __init__(self, solenoidDict):
        self.testing = True if testing else False
        self.solenoidDict = solenoidDict
        self.on = False

        if not self.testing:
            self.buzzer = Buzzer(pin='BOARD7')
            for nozzle, boardPin in self.solenoidDict.items():
                self.solenoidDict[nozzle] = OutputDevice(pin='BOARD{}'.format(boardPin))

        else:
            self.buzzer = TestBuzzer()
            for nozzle, boardPin in self.solenoidDict.items():
                self.solenoidDict[nozzle] = TestRelay(boardPin)


    def relay_on(self, solenoidNumber, verbose=True):
        relay = self.solenoidDict[solenoidNumber]
        relay.on()

        if verbose:
            print("Solenoid {} ON".format(solenoidNumber))

    def relay_off(self, solenoidNumber, verbose=True):
        relay = self.solenoidDict[solenoidNumber]
        relay.off()

        if verbose:
            print("Solenoid {} OFF".format(solenoidNumber))

    def beep(self, duration=0.2, repeats=2):
        self.buzzer.beep(on_time=duration, off_time=(duration / 2), n=repeats)

    def all_on(self):
        for nozzle in self.solenoidDict.keys():
            self.relay_on(nozzle)

    def all_off(self):
        for nozzle in self.solenoidDict.keys():
            self.relay_off(nozzle)

    def remove(self, solenoidNumber):
        self.solenoidDict.pop(solenoidNumber, None)

    def clear(self):
        self.solenoidDict = {}

    def stop(self):
        self.clear()
        self.all_off()

# this class does the hard work of receiving detection 'jobs' and queuing them to be actuated. It only turns a nozzle on
# if the sprayDur has not elapsed or if the nozzle isn't already on.
class Controller:
    def __init__(self, nozzleDict):
        self.nozzleDict = nozzleDict
        # instantiate relay control with supplied nozzle dictionary to map to correct board pins
        self.solenoid = RelayControl(self.nozzleDict)
        self.nozzleQueueDict = {}
        self.nozzleconditionDict = {}

        # start the logger and log file
        self.logger = Logger(name="weed_log.txt", saveDir="logs")

        # create a job queue and Condition() for each nozzle
        print("[INFO] Setting up nozzles...")
        for nozzle in range(0, len(self.nozzleDict)):
            self.nozzleQueueDict[nozzle] = collections.deque(maxlen=5)
            self.nozzleconditionDict[nozzle] = Condition()

            # create the consumer threads, setDaemon and start the threads.
            nozzleThread = Thread(target=self.consumer, args=[nozzle])
            nozzleThread.setDaemon(True)
            nozzleThread.start()

        time.sleep(1)
        print("[INFO] Setup complete. Starting spraying.")
        self.solenoid.beep(duration=0.5)

    def receive(self, nozzle, timeStamp, location=0, delay=0, duration=1):
        """
        this method adds a new spray job to specified nozzle queue. GPS location data etc to be added. Time stamped
        records the true time of weed detection from main thread, which is compared to time of nozzle activation for accurate
        on durations. There will be a minimum on duration of this processing speed ~ 0.3s. Will default to 0 though.
        :param nozzle: nozzle number (zero based)
        :param timeStamp: this is the time of detection
        :param location: GPS functionality to be added here
        :param delay: on delay to be added in the future
        :param duration: duration of spray
        """
        inputQMessage = [nozzle, timeStamp, delay, duration, location]
        inputQ = self.nozzleQueueDict[nozzle]
        inputCondition = self.nozzleconditionDict[nozzle]
        # notifies the consumer thread when something has been added to the queue
        with inputCondition:
            inputQ.append(inputQMessage)
            inputCondition.notify()

        line = "nozzle: {} | time: {} | location {} | delay: {} | duration: {}".format(nozzle, timeStamp, delay, duration, location)
        self.logger.log_line(line)

    def consumer(self, nozzle):
        """
        Takes only one parameter - nozzle, which enables the selection of the deque, condition from the dictionaries.
        The consumer method is threaded for each nozzle and will wait until it is notified that a new job has been added
        from the receive method. It will then compare the time of detection with time of spraying to activate that nozzle
        for requried length of time.
        :param nozzle: nozzle vlaue
        """
        self.running = True
        inputCondition = self.nozzleconditionDict[nozzle]
        inputCondition.acquire()
        nozzleOn = False
        nozzleQueue = self.nozzleQueueDict[nozzle]
        while self.running:
            while nozzleQueue:
                # nozzle, timeStamp, delay, duration, location
                sprayJob = nozzleQueue.popleft()
                inputCondition.release()
                # difference between detection and current time
                timeDiff = time.time() - sprayJob[1]
                # check to make sure time is positive
                onDur = 0 if (sprayJob[3] - timeDiff <= 0) else (sprayJob[3] - timeDiff)

                if not nozzleOn:
                    # zero delay if time has already expired
                    delay = 0 if onDur == 0 else (sprayJob[2] - timeDiff)
                    try:
                        time.sleep(delay) # add in the delay variable
                    except ValueError:
                        time.sleep(0)

                    self.solenoid.relay_on(nozzle, verbose=True)
                    nozzleOn = True
                try:
                    time.sleep(onDur)
                    self.logger.log_line(
                        '[INFO] onDur {} for nozzle {} received.'.format(onDur, nozzle))

                except ValueError:
                    time.sleep(0)
                    self.logger.log_line(
                        '[ERROR] negative onDur {} for nozzle {} received. Turning on for 0 seconds.'.format(onDur,
                                                                                                             nozzle))
                inputCondition.acquire()
            if len(nozzleQueue) == 0:
                self.solenoid.relay_off(nozzle, verbose=True)
                nozzleOn = False

            inputCondition.wait()
