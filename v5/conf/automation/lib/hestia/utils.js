'use strict';

(function(context) {
  'use strict';

  // Logger config
  var LOG_ROOT = "org.openhab.core.model.script.";
  var LOGGER_NAME = "hestia";

  var logMessage = function(value) {
    return (value === undefined || value === null) ? "null" : String(value);
  };

  var slf4j = function(name) {
    return Java.type("org.slf4j.LoggerFactory").getLogger(LOG_ROOT+name);
  };

  context.logDebug = function(name, message) {
    if (message === undefined) { message = name; name = LOGGER_NAME; }
    slf4j(name).debug(logMessage(message));
  };

  context.logInfo = function(name, message) {
    if (message === undefined) { message = name; name = LOGGER_NAME; }
    slf4j(name).info(logMessage(message));
  };

  context.logWarn = function(name, message) {
    if (message === undefined) { message = name; name = LOGGER_NAME; }
    slf4j(name).warn(logMessage(message));
  };

  context.logError = function(name, message) {
    if (message === undefined) { message = name; name = LOGGER_NAME; }
    slf4j(name).error(logMessage(message));
  };

  // Compatibility shim for rules written against the openHAB 2 "logger" object
  context.logger = {
    trace: function(message) { slf4j(LOGGER_NAME).trace(logMessage(message)); },
    debug: function(message) { slf4j(LOGGER_NAME).debug(logMessage(message)); },
    info: function(message) { slf4j(LOGGER_NAME).info(logMessage(message)); },
    warn: function(message) { slf4j(LOGGER_NAME).warn(logMessage(message)); },
    error: function(message) { slf4j(LOGGER_NAME).error(logMessage(message)); }
  };


  // Timers
  var FrameworkUtil = Java.type("org.osgi.framework.FrameworkUtil");
  var ScriptExecutionClass = Java.type("org.openhab.core.automation.module.script.action.ScriptExecution").class;
  var bundleContext = FrameworkUtil.getBundle(ScriptExecutionClass).getBundleContext();
  var scriptExecutionRef = bundleContext.getServiceReference("org.openhab.core.automation.module.script.action.ScriptExecution");
  context.ScriptExecution = bundleContext.getService(scriptExecutionRef);
  context.createTimerSecs = function(secs, runnable) {
    try {
      var time = Java.type("java.time.ZonedDateTime").now().plusSeconds(secs);
      var task = new (Java.type("java.lang.Runnable"))({ run: runnable });

      return context.ScriptExecution.createTimer(time, task);
    }
    catch(err){
      context.logError("createTimer", "Error creating timer: " + __LINE__ + " Error: " + err);
    }
  };

  context.sleep = function(secs) {
    Java.type("java.lang.Thread").sleep(secs*1000);
  };

  context.waitForUpdate = function(item, maxSecs) {
    var count = 0;
    while(count < maxSecs && (items[item] == NULL || items[item] == UNDEF)){
      count++;
      Java.type("java.lang.Thread").sleep(1000);
    }
  };

  context.waitForUpdateState = function(item, state, maxSecs) {
    var count = 0;
    while(count < maxSecs && (items[item] != state)){
      count++;
      Java.type("java.lang.Thread").sleep(1000);
    }
  };

  context.ExecUtil = Java.type("org.openhab.core.io.net.exec.ExecUtil");
  context.executeCommandLine = function(cmd, timeout){
    var result = context.ExecUtil.executeCommandLineAndWaitResponse(Java.type("java.time.Duration").ofMillis(timeout), "/bin/sh", "-c", cmd);
    return (result === null) ? result : result.replace(/[\r\n]+$/, "");
  };

  context.commandIfDifferent = function(item, cmd){
    if(items[item] != cmd) {
      events.sendCommand(item, cmd);
    }
  };
})(this);
