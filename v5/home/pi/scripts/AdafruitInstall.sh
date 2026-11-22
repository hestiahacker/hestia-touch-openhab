sudo apt-get update && apt-get upgrade;
sudo apt-get install build-essential python3-pip python3-dev python3-smbus git;
mkdir /home/pi/git/;
cd /home/pi/git/;
git clone https://github.com/adafruit/Adafruit_Python_DHT.git;
cd Adafruit_Python_DHT;
sudo python3 setup.py install;
