#! /usr/bin/env bash
# On Ubuntu 14.04, amd64, Ubuntu provided ami image 
# ami-80778be8

source ${PWD}/jboxcommon.sh
NGINX_VER=1.7.2.1
NGINX_INSTALL_DIR=/usr/local/openresty
NGINX_SUDO=sudo
mkdir -p $NGINX_INSTALL_DIR

DOCKER_IMAGE=juliabox/juliabox
DOCKER_IMAGE_VER=$(grep "^# Version:" docker/IJulia/Dockerfile | cut -d":" -f2)

function usage {
  echo
  echo 'Usage: ./setup.sh -u <admin_username> optional_args'
  echo ' -u  <username> : Mandatory admin username. If -g option is used, this must be the complete Google email-id'
  echo ' -g             : Use Google OAuth2 for user authentication. Options -k and -s must be specified.'
  echo ' -k  <key>      : Google OAuth2 key (client id).'
  echo ' -s  <secret>   : Google OAuth2 client secret.'
  echo ' -d             : Only recreate docker image - do not install/update other software'
  echo ' -n  <num>      : Maximum number of active containers. Default 2.'
  echo ' -v  <num>      : Maximum number of mountable volumes. Default 2.'
  echo ' -t  <seconds>  : Auto delete containers older than specified seconds. 0 means never expire. Default 0.'
  echo ' -i             : Install in an invite only mode.'
  echo
  echo 'Post setup, additional configuration parameters may be set in jbox.user '
  echo 'Please see README.md (https://github.com/JuliaLang/JuliaBox) for more details '
  
  exit 1
}

function sysinstall_pystuff {
    sudo easy_install tornado
    sudo easy_install futures
    sudo easy_install google-api-python-client
    sudo pip install PyDrive
    sudo pip install boto
    sudo pip install pycrypto
    sudo pip install psutil

    git clone https://github.com/dotcloud/docker-py 
    cd docker-py
    sudo python setup.py install
    cd ..
    sudo rm -Rf docker-py
}

function sysinstall_resty {
    echo "Building nginx openresty for install at ${NGINX_INSTALL_DIR} ..."
    mkdir -p resty
    wget -P resty http://openresty.org/download/ngx_openresty-${NGINX_VER}.tar.gz
    cd resty
    tar -xvzf ngx_openresty-${NGINX_VER}.tar.gz
    cd ngx_openresty-${NGINX_VER}
    ./configure --prefix=${NGINX_INSTALL_DIR}
    make
    ${NGINX_SUDO} make install
    cd ../..
    rm -Rf resty
    ${NGINX_SUDO} mkdir -p ${NGINX_INSTALL_DIR}/lualib/resty/http
    ${NGINX_SUDO} cp -f libs/lua-resty-http-simple/lib/resty/http/simple.lua ${NGINX_INSTALL_DIR}/lualib/resty/http/
}

function sysinstall_libs {
    # Stuff required for docker, openresty, and tornado
    sudo apt-get -y update
    sudo apt-get -y install build-essential libreadline-dev libncurses-dev libpcre3-dev libssl-dev netcat git python-setuptools supervisor python-dev python-isodate python-pip python-tz
}

function sysinstall_docker {
    # INSTALL docker as per http://docs.docker.io/en/latest/installation/ubuntulinux/
    sudo apt-get -y update
    sudo apt-get -y install linux-image-extra-`uname -r`
    sudo sh -c "wget -qO- https://get.docker.io/gpg | apt-key add -"
    sudo sh -c "echo deb http://get.docker.io/ubuntu docker main > /etc/apt/sources.list.d/docker.list"
    sudo apt-get -y update
    sudo apt-get -y install lxc-docker

    # docker stuff
    sudo gpasswd -a $USER docker
}

function configure_docker {
    # On EC2 we use the ephemeral storage for the images and the docker aufs filsystem store.
    sudo mkdir -p /mnt/docker
    sudo service docker stop
    if grep -q "^DOCKER_OPTS" /etc/default/docker ; then
      echo "/etc/default/docker has an entry for DOCKER_OPTS..."
      echo "Please ensure DOCKER_OPTS has appropriate options"
    else
      # set loop data size to that required for max containers plus 5 additional
      LOOPDATASZ=$(((NUM_LOCALMAX+5)*3))
      echo "Configuring docker to use"
      echo "    -  /mnt/docker for image/container storage"
      echo "    - devicemapper fs"
      echo "    - base image size 3GB"
      echo "    - loopdatasize ${LOOPDATASZ}GB"
      sudo sh -c "echo 'DOCKER_OPTS=\"--storage-driver=devicemapper --storage-opt dm.basesize=3G --storage-opt dm.loopdatasize=${LOOPDATASZ}G -g /mnt/docker \"' >> /etc/default/docker"
    fi
    sudo service docker start

    # Wait for the docker process to bind to the required ports
    sleep 1
}

function build_docker_image {
    echo "Building docker image ${DOCKER_IMAGE}:${DOCKER_IMAGE_VER} ..."
    sudo docker build --rm=true -t ${DOCKER_IMAGE}:${DOCKER_IMAGE_VER} docker/IJulia/
    sudo docker tag ${DOCKER_IMAGE}:${DOCKER_IMAGE_VER} ${DOCKER_IMAGE}:latest
}

function pull_docker_image {
    echo "Pulling docker image ${DOCKER_IMAGE}:${DOCKER_IMAGE_VER} ..."
    sudo docker pull tanmaykm/juliabox:${DOCKER_IMAGE_VER}
    sudo docker tag tanmaykm/juliabox:${DOCKER_IMAGE_VER} ${DOCKER_IMAGE}:${DOCKER_IMAGE_VER}
    sudo docker tag tanmaykm/juliabox:${DOCKER_IMAGE_VER} ${DOCKER_IMAGE}:latest
}

function make_user_home {
	${PWD}/docker/mk_user_home.sh
}

function gen_sesskey {
    echo "Generating random session validation key"
    SESSKEY=`< /dev/urandom tr -dc _A-Z-a-z-0-9 | head -c32`
    echo $SESSKEY > .jbox_session_key
}

function configure_resty_tornado {
    echo "Setting up nginx.conf ..."
    sed  s/\$\$NGINX_USER/$USER/g $NGINX_CONF_DIR/nginx.conf.tpl > $NGINX_CONF_DIR/nginx.conf
    sed  -i s/\$\$ADMIN_KEY/$1/g $NGINX_CONF_DIR/nginx.conf

    if [ ! -e .jbox_session_key ]
    then
        gen_sesskey
    fi
    SESSKEY=`cat .jbox_session_key`

    sed  -i s/\$\$SESSKEY/$SESSKEY/g $NGINX_CONF_DIR/nginx.conf 
    sed  s/\$\$SESSKEY/$SESSKEY/g $TORNADO_CONF_DIR/tornado.conf.tpl > $TORNADO_CONF_DIR/tornado.conf

    if test $OPT_GOOGLE -eq 1; then
        sed  -i s/\$\$GAUTH/True/g $TORNADO_CONF_DIR/tornado.conf
    else
        sed  -i s/\$\$GAUTH/False/g $TORNADO_CONF_DIR/tornado.conf
    fi
    if test $OPT_INVITE -eq 1; then
        sed  -i s/\$\$INVITE/True/g $TORNADO_CONF_DIR/tornado.conf
    else
        sed  -i s/\$\$INVITE/False/g $TORNADO_CONF_DIR/tornado.conf
    fi

    sed  -i s/\$\$ADMIN_USER/$ADMIN_USER/g $TORNADO_CONF_DIR/tornado.conf
    sed  -i s/\$\$NUM_LOCALMAX/$NUM_LOCALMAX/g $TORNADO_CONF_DIR/tornado.conf
    sed  -i s/\$\$NUM_DISKSMAX/$NUM_DISKSMAX/g $TORNADO_CONF_DIR/tornado.conf
    sed  -i s/\$\$EXPIRE/$EXPIRE/g $TORNADO_CONF_DIR/tornado.conf
    sed  -i s,\$\$DOCKER_IMAGE,$DOCKER_IMAGE,g $TORNADO_CONF_DIR/tornado.conf
    sed  -i s,\$\$CLIENT_SECRET,$CLIENT_SECRET,g $TORNADO_CONF_DIR/tornado.conf
    sed  -i s,\$\$CLIENT_ID,$CLIENT_ID,g $TORNADO_CONF_DIR/tornado.conf
    
    sed  s,\$\$JBOX_DIR,$PWD,g host/juliabox_logrotate.conf.tpl > host/juliabox_logrotate.conf
}


OPT_INSTALL=1
OPT_GOOGLE=0
OPT_INVITE=0
NUM_LOCALMAX=2
NUM_DISKSMAX=2
EXPIRE=0

while getopts  "u:idgn:v:t:k:s:" FLAG
do
  if test $FLAG == '?'
     then
        usage

  elif test $FLAG == 'u'
     then
        ADMIN_USER=$OPTARG

  elif test $FLAG == 'd'
     then
        OPT_INSTALL=0

  elif test $FLAG == 'g'
     then
        OPT_GOOGLE=1

  elif test $FLAG == 'i'
     then
        OPT_INVITE=1

  elif test $FLAG == 'n'
     then
        NUM_LOCALMAX=$OPTARG

  elif test $FLAG == 'v'
     then
        NUM_DISKSMAX=$OPTARG

  elif test $FLAG == 't'
     then
        EXPIRE=$OPTARG

  elif test $FLAG == 'k'
     then
        CLIENT_ID=$OPTARG

  elif test $FLAG == 's'
     then
        CLIENT_SECRET=$OPTARG
  fi
done

if test -v $ADMIN_USER
  then
    usage
fi

#echo $ADMIN_USER $OPT_INSTALL $OPT_GOOGLE


if test $OPT_INSTALL -eq 1; then
    sysinstall_libs
    sysinstall_docker
    sysinstall_resty
    sysinstall_pystuff
    configure_docker
    # Wait for the docker process to bind to the required ports
    sleep 1
fi

pull_docker_image
make_user_home
configure_resty_tornado

echo
echo "DONE!"
 
