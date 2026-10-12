"""Source-bound Node controls for real session JS and read-only locale foundation.

The DOM and HTTP are explicit synthetic fixtures, not browser or production
acceptance. The shipped JS and foundation execute unchanged. CLI evidence is
fresh external 3FS data; runtime stays under /data/tmp. No agent is spawned.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[4]
BASE = "9c4abdac2906111d49f0c39a437ca4cef7abb9b3"
FOUNDATION = Path("/data/dev/wt-opencad-locale-foundation-20261012")
FOUNDATION_HEAD = "97b80e675f39e23faffcb8c0f6bb8706a8238f0b"
CATALOG_HOME = Path("/data/dev/wt-opencad-panel-catalog-r2-20261012")
SCRIPT = "oss-alternatives/apps/opencad/web/js/project-session.js"
TEST = "oss-alternatives/apps/opencad/tests/test_project_session_locale.py"
DOC = "docs/design/project-session-locale.md"
LOCALE = "oss-alternatives/apps/opencad/web/js/locale-service.js"
CATALOG = "oss-alternatives/apps/opencad/web/panel-i18n.json"
PLACEHOLDER = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
OWNED = {SCRIPT, TEST, DOC}
MARKER = "PROJECT_SESSION_LOCALE_R2"
CASES = (
    "anonymous", "login-copy", "attributes", "switch-state", "queue-cas",
    "pending-replay", "expired-recovery", "wrong-account", "server-detail",
    "view-detail", "local-file", "late-response", "late-body", "late-401-body",
    "late-ready", "late-activation", "read-shield", "damaged-shield",
    "public-alert-contract",
    "missing-key", "missing-args", "missing-api", "load-failure",
    "invalid-snapshot", "binding-release", "project-title", "logout-detail",
    "account-storage", "locale-response", "late-recovery", "all-keymap",
)

NODE = r"""
"use strict";
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
const [root, foundation, dataPath, mode] = process.argv.slice(2);
const data = JSON.parse(fs.readFileSync(dataPath, "utf8"));
let checks = 0;
let complete = false;
process.on("beforeExit",()=>{
  if(!complete){complete=true;console.error(JSON.stringify({mode,verdict:"FAIL",checks,
    first_cause:"fixture_async_incomplete"}));process.exitCode=1;}
});
function ok(value, message) { checks++; assert.ok(value, message); }
function eq(a, b, message) { checks++; assert.deepEqual(a, b, message); }
const PREFIX = "panel.session.";
function attrName(key) {return "data-" + key.replace(/[A-Z]/g, m => "-" + m.toLowerCase());}
class Element {
  constructor(tag, doc) {
    this.tagName = tag.toUpperCase(); this.doc = doc; this.children = [];
    this.parentNode = null; this.attrs = new Map(); this.listeners = new Map();
    this.text = ""; this.value = ""; this.disabled = false; this.hidden = false;
    this.inert = false; this.selectionStart = 0; this.selectionEnd = 0;
    const self = this;
    this.style = {visibility:"", priority:"",
      setProperty(k,v,p="") {ok(k==="visibility","known style"); this[k]=v;this.priority=p;},
      getPropertyPriority(k) {return this.priority;}};
    this.dataset = new Proxy({}, {
      get(_,k) {return self.attrs.get(attrName(k));},
      set(_,k,v) {self.attrs.set(attrName(k),String(v));return true;},
      deleteProperty(_,k) {self.attrs.delete(attrName(k));return true;}
    });
  }
  get isConnected() {return this===this.doc.body || !!(this.parentNode&&this.parentNode.isConnected);}
  set className(v) {this.setAttribute("class",v);} get className() {return this.getAttribute("class")||"";}
  set placeholder(v) {this.setAttribute("placeholder",v);} get placeholder() {return this.getAttribute("placeholder")||"";}
  get textContent() {return this.text + this.children.map(n=>n.textContent).join("");}
  set textContent(v) {this.text=String(v);this.replaceChildren();}
  setAttribute(k,v) {this.attrs.set(k,String(v));}
  getAttribute(k) {return this.attrs.has(k)?this.attrs.get(k):null;}
  hasAttribute(k) {return this.attrs.has(k);} removeAttribute(k) {this.attrs.delete(k);}
  getAttributeNames() {return Array.from(this.attrs.keys());}
  append(...nodes) {
    for(const n of nodes){if(n.parentNode)n.remove();n.parentNode=this;this.children.push(n);}
    if(this.tagName==="SELECT"&&!this.value&&this.children.length)this.value=this.children[0].value;
    this.doc.changed();
  }
  prepend(n) {if(n.parentNode)n.remove();n.parentNode=this;this.children.unshift(n);this.doc.changed();}
  replaceChildren(...nodes) {for(const n of this.children)n.parentNode=null;this.children=[];if(nodes.length)this.append(...nodes);}
  remove() {if(this.parentNode){const p=this.parentNode;p.children=p.children.filter(n=>n!==this);this.parentNode=null;this.doc.changed();}}
  matches(selector) {
    return selector.split(",").some(part=>{
      const s=part.trim();
      if(s.startsWith("."))return this.className.split(" ").includes(s.slice(1));
      if(s.startsWith("#"))return this.getAttribute("id")===s.slice(1);
      const m=/^([a-z][a-z0-9]*)?(?:\[([\w-]+)(?:=["']?([^"'\]]+)["']?)?\])?$/.exec(s);
      if(!m)throw new Error("fixture_unknown_selector:"+s);
      return (!m[1]||this.tagName===m[1].toUpperCase())&&
        (!m[2]||(this.hasAttribute(m[2])&&(m[3]===undefined||this.getAttribute(m[2])===m[3])));
    });
  }
  querySelectorAll(selector) {
    const all=[];function visit(n){for(const c of n.children){all.push(c);visit(c);}}visit(this);
    return all.filter(n=>selector.split(",").some(s=>{
      const parts=s.trim().split(/\s+/);if(!n.matches(parts.pop()))return false;
      let parent=n.parentNode;
      while(parts.length){const part=parts.pop();while(parent&&!parent.matches(part))parent=parent.parentNode;if(!parent)return false;parent=parent.parentNode;}
      return true;
    }));
  }
  querySelector(s) {return this.querySelectorAll(s)[0]||null;}
  addEventListener(type,fn) {if(!this.listeners.has(type))this.listeners.set(type,[]);this.listeners.get(type).push(fn);}
  async emit(type) {const e={type,preventDefault(){}};for(const fn of this.listeners.get(type)||[])await fn(e);}
  click() {return this.disabled?Promise.resolve():this.emit("click");}
  showModal() {this.setAttribute("open","");} close() {this.removeAttribute("open");}
  focus() {this.doc.activeElement=this;} blur() {this.doc.activeElement=null;}
}
function deferred() {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function response(status,value) {return new Response(JSON.stringify(value),{status,headers:{"Content-Type":"application/json"}});}
async function settle() {for(let i=0;i<5;i++)await new Promise(setImmediate);}
async function fixture() {
  const doc={readyState:"complete",activeElement:null,observers:[],changed(){for(const fn of this.observers)fn();},
    createElement(tag){return new Element(tag,this);},
    querySelectorAll(s){return this.body.querySelectorAll(s);},querySelector(s){return this.body.querySelector(s);},
    addEventListener(){}};
  doc.body=new Element("body",doc);
  const workspace=doc.createElement("main");workspace.setAttribute("id","workspace");
  const draft=doc.createElement("input");draft.value="draft-original";draft.selectionStart=2;draft.selectionEnd=4;
  workspace.append(draft);doc.body.append(workspace);
  const events=new Map(), state={auth:false,owner:"alice",project:"p-alice",locale:"ja",rev:7,
    calls:[],updates:[],receipts:[],delays:[],mode:"normal",delayPath:null,bodyPath:null,reloads:0};
  const storage=new Map([["opencad-project-id",JSON.stringify({owner_id:"bob",project_id:"p-bob"})],
    ["opencad-locale","zh-CN"]]);
  const windows={document:doc,
    addEventListener(t,f){if(!events.has(t))events.set(t,[]);events.get(t).push(f);},
    dispatchEvent(e){if(e.type==="opencad:project-commit")state.receipts.push(e.detail);for(const f of events.get(e.type)||[])f(e);},
    fetch:async(input,init)=>{
      const path=new URL(typeof input==="string"?input:input.url,"http://fixture").pathname;
      if(path==="/web/panel-i18n.json") {
        const pack=structuredClone(data.catalog);
        if(mode==="missing-key")for(const p of Object.values(pack.locales))delete p[PREFIX+"confirm_edit"];
        if(mode==="missing-args")for(const p of Object.values(pack.locales))p[PREFIX+"confirm_edit"]+=" {unexpected}";
        if(mode==="load-failure")return response(503,{ok:false});
        return response(200,pack);
      }
      const body=init&&init.body?JSON.parse(init.body):null;
      state.calls.push({path,body:body&&path.startsWith("/api/auth/")?{credential_fields:Object.keys(body)}:body,
        project:init&&init.headers?new Headers(init.headers).get("X-OpenCAD-Project"):null});
      let status=200,value;
      if(path==="/api/auth/me"){status=state.auth?200:401;value=state.auth?{ok:true,user:{user_id:state.owner}}:{ok:false,detail:"raw-login-detail"};}
      else if(path==="/api/auth/login"){state.auth=true;value={ok:true,user:{user_id:state.owner}};}
      else if(path==="/api/auth/logout"){
        if(state.mode==="logout-failed"){status=503;value={ok:false,detail:"raw-logout-detail"};}
        else{state.auth=false;value={ok:true};}
      }
      else if(path==="/api/projects")value={ok:true,projects:[{project_id:state.project,title:"User title <b>raw</b>"}]};
      else {
        value={ok:true,project_id:state.project,revision:state.rev,sha256:"a".repeat(64),
          locale:state.locale,strings:{"cmd.label":"Command"}};
        if(state.mode==="offline")throw new Error("raw-offline-detail");
        if(state.mode==="401"||state.mode==="401-body"){status=401;value={ok:false,detail:"raw-401-detail"};}
        if(state.mode==="403"){status=403;value={ok:false,detail:"raw-403-detail"};}
        if(state.mode==="409"){status=409;value={ok:false,detail:"raw-conflict-detail"};}
        if(state.mode==="damaged")return new Response("{",{headers:{"Content-Type":"application/json"}});
        if(body&&body.request_id&&status===200){state.rev++;value.revision=state.rev;value.request_id=body.request_id;}
      }
      let result=response(status,value);
      if(state.bodyPath===path||state.mode==="401-body"){
        const d=deferred();state.delays.push({path,resolve:()=>d.resolve(value)});
        result.json=()=>d.promise;result.clone=()=>result;
      }
      if(state.delayPath===path){const d=deferred();state.delays.push({path,resolve:()=>d.resolve(result)});return d.promise;}
      return result;
    }};
  const location={href:"http://fixture/",origin:"http://fixture",reload(){state.reloads++;}};
  const context=vm.createContext({window:windows,document:doc,location,console,URL,Response,Headers,
    Request:class extends Request{constructor(input,init){super(typeof input==="string"?new URL(input,location.href):input,init);}},
    crypto:require("crypto").webcrypto,CustomEvent:class{constructor(type,options={}){this.type=type;this.detail=options.detail;}},
    MutationObserver:class{constructor(f){this.f=f;}observe(){doc.observers.push(this.f);}},
    sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    localStorage:{getItem(){throw Error("locale_storage_forbidden");}},
    setTimeout(f){f();return 1;},Promise,Map,Set,Array,Object,Number,String,JSON,Error});
  doc.currentScript={src:"http://fixture/web/js/locale-service.js"};
  vm.runInContext(fs.readFileSync(foundation+"/oss-alternatives/apps/opencad/web/js/locale-service.js","utf8"),context,{filename:"locale-service.js"});
  const service=windows.OpenCADLocale;
  service.ready.catch(()=>{});
  windows.OpenCADLocale={...service,update(v){state.updates.push(structuredClone(v));return service.update(v);}};
  vm.runInContext(fs.readFileSync(root+"/oss-alternatives/apps/opencad/web/js/project-session.js","utf8"),context,{filename:"project-session.js"});
  await settle();
  function nodes(){const a=[];function walk(n){a.push(n);for(const c of n.children)walk(c);}walk(doc.body);return a;}
  const entry=()=>nodes().find(n=>n.hasAttribute("data-session-entry"));
  const recovery=()=>nodes().find(n=>n.hasAttribute("data-session-recovery"));
  const named=(parent,name)=>parent.querySelectorAll("input").find(n=>n.name===name);
  const key=(suffix)=>nodes().find(n=>n.getAttribute("data-panel-i18n")===PREFIX+suffix);
  const bar=()=>nodes().find(n=>n.className==="project-status");
  const notice=()=>bar().children[0];
  async function login(open=true){
    const e=entry();named(e,"username").value="typed-account";named(e,"password").value="typed-input";
    await e.querySelector("form").emit("submit");await settle();
    if(open){await e.querySelectorAll("button")[0].click();await settle();await windows.OpenCADProjectSession.ready;}
  }
  async function api(path="/api/state",body){
    try{const r=await windows.fetch(path,body===undefined?undefined:{method:"POST",body:JSON.stringify(body)});
      return {status:r.status,value:await r.json()};}catch(e){return {error:e.message};}
  }
  async function switchTo(code){service.update({locale:code,strings:{"cmd.label":"Command"}});await settle();}
  function preserve(){return JSON.stringify({calls:state.calls,receipts:state.receipts,updates:state.updates,
    value:draft.value,selection:[draft.selectionStart,draft.selectionEnd],focus:doc.activeElement===draft,
    locked:doc.body.dataset.opencadSessionLocked,visibility:workspace.style.visibility,inert:workspace.inert,
    controls:nodes().filter(n=>n.tagName==="INPUT"||n.tagName==="BUTTON"||n.tagName==="SELECT")
      .map(n=>({value:n.value,disabled:n.disabled,hidden:n.hidden})),storage:Array.from(storage)});}
  return {doc,state,windows,service,workspace,draft,nodes,entry,recovery,named,key,bar,notice,login,api,switchTo,preserve,storage};
}
async function run() {
  const f=await fixture(), {state,doc,service,windows,draft,workspace}=f;
  if(mode==="anonymous"){
    eq(f.entry().querySelector("h1").textContent,data.catalog.locales.en[PREFIX+"heading"],"explicit English default");
    eq(state.updates.length,0,"anonymous no response locale");
    ok(f.named(f.entry(),"username").hasAttribute("data-panel-i18n-aria"),"anonymous translated aria");
  } else if(mode==="login-copy"){
    await f.login(false);await f.switchTo("ja");
    eq(f.entry().querySelector("h1").textContent,data.catalog.locales.ja[PREFIX+"heading"],"login heading changes");
    eq(f.entry().querySelector("select").getAttribute("aria-label"),data.catalog.locales.ja[PREFIX+"project"],"project aria");
    ok(f.key("create_project"),"project creation key");
  } else if(mode==="all-keymap"){
    await service.ready;
    for(const [code,pack] of Object.entries(data.catalog.locales)){
      await f.switchTo(code);
      for(const [key,text] of Object.entries(pack)){
        const args={};for(const m of text.matchAll(/\{(\w+)\}/g))args[m[1]]="raw-argument";
        eq(service.t(key,args),text.replace(/\{(\w+)\}/g,"raw-argument"),"real foundation key "+key);
      }
    }
  } else if(["missing-key","missing-args","missing-api","load-failure","invalid-snapshot"].includes(mode)){
    if(mode==="missing-api")delete windows.OpenCADLocale;
    if(mode==="invalid-snapshot")windows.OpenCADLocale.snapshot=()=>({locale:null,status:"error"});
    await f.login();await settle();
    const code=doc.body.dataset.opencadSessionLocaleError;
    const expected={"missing-key":"locale_missing_key","missing-args":"locale_argument_set_mismatch",
      "missing-api":"session_locale_api_unavailable","load-failure":"locale_package_http_error",
      "invalid-snapshot":"session_locale_snapshot_invalid"}[mode];
    ok(code&&code.includes(expected),"exact fail-loud first cause "+expected+" actual="+String(code));
    const localeAlert=f.bar().querySelectorAll("output").find(n=>!n.hidden&&n.getAttribute("role")==="alert");
    ok(localeAlert,"visible locale error");
    ok(localeAlert&&/@ panel\.session\./.test(localeAlert.textContent),"first cause key coordinate retained");
    eq((await f.api("/api/save",{})).status,200,"locale failure does not alter CAS");
    eq(draft.value,"draft-original","draft retained");
  } else {
    await f.login();
    if(mode==="attributes"){
      eq(state.updates[0].locale,"ja","authorized project locale");
      eq(state.updates[0].strings,{"cmd.label":"Command"},"only language fields");
      eq(f.key("logout").textContent,data.catalog.locales.ja[PREFIX+"logout"],"real translation");
    } else if(mode==="switch-state"){
      draft.focus();const before=f.preserve();await f.switchTo("zh-CN");eq(f.preserve(),before,"no state mutation");
      eq(f.key("logout").textContent,data.catalog.locales["zh-CN"][PREFIX+"logout"]);
    } else if(mode==="queue-cas"){
      state.delayPath="/api/save";
      const one=f.api("/api/save",{path:"/ignored"}),two=f.api("/api/save",{});
      await settle();eq(state.calls.filter(c=>c.path==="/api/save").length,1,"queue order");
      await f.switchTo("en");state.delayPath=null;state.delays.shift().resolve();
      eq((await one).status,200);eq((await two).status,200);
      const calls=state.calls.filter(c=>c.path==="/api/save");
      eq(calls.map(c=>c.body.expected_revision),[7,8],"CAS advanced only by receipts");
      ok(!("path" in calls[0].body));ok(calls[0].body.request_id!==calls[1].body.request_id);
      eq(state.receipts.length,2);eq(state.receipts[0].base.expected_revision,7);
    } else if(mode==="pending-replay"||mode==="view-detail"){
      state.mode="offline";await f.api("/api/save",{});
      const original=state.calls.find(c=>c.path==="/api/save").body;
      await f.switchTo("en");
      if(mode==="pending-replay") {
        const before=f.preserve();await f.switchTo("ja");eq(f.preserve(),before);
        ok((await f.api("/api/save",{})).error.includes(data.catalog.locales.ja[PREFIX+"previous_pending"]));
      }
      state.mode="normal";await f.bar().querySelectorAll("button")[0].click();await settle();
      eq(state.calls.filter(c=>c.path==="/api/save")[1].body,original,"same receipt replay");
      ok(f.notice().textContent.includes("workspace_refresh_unavailable"),"original view detail retained");
      ok(f.notice().getAttribute("data-panel-i18n-args").includes("detail"),"named detail binding");
      const before=f.preserve();await f.switchTo("zh-CN");eq(f.preserve(),before);ok(f.notice().textContent.includes("workspace_refresh_unavailable"));
    } else if(mode==="expired-recovery"||mode==="wrong-account"||mode==="late-recovery"){
      state.mode="offline";await f.api("/api/save",{});
      state.mode="401";eq((await f.api()).status,401);await f.bar().querySelectorAll("button")[2].click();
      const dialog=f.recovery();ok(dialog,"recovery opened");
      f.named(dialog,"username").value="typed-original";f.named(dialog,"password").value="typed-input";
      const before=f.preserve();await f.switchTo("zh-CN");eq(f.preserve(),before,"locked recovery input/shield");
      eq(dialog.querySelector("h1").textContent,data.catalog.locales["zh-CN"][PREFIX+"recovery_heading"]);
      if(mode==="wrong-account"){
        state.owner="bob";state.mode="normal";await dialog.querySelector("form").emit("submit");await settle();
        eq(state.updates.length,1);eq(state.calls.filter(c=>c.path==="/api/save").length,1);
        eq(doc.body.dataset.opencadSessionLocked,"true");
        eq(f.named(dialog,"password").value,"");ok(dialog.querySelector('[role="alert"]').textContent.includes(data.catalog.locales["zh-CN"][PREFIX+"wrong_account"]));
      }
      if(mode==="late-recovery"){
        state.delayPath="/api/auth/login";const restoring=dialog.querySelector("form").emit("submit");await settle();
        windows.dispatchEvent({type:"pageshow",persisted:true});
        state.delays.shift().resolve();await restoring;await settle();
        eq(doc.body.dataset.opencadSessionLocked,"true");eq(state.updates.length,1);
      }
    } else if(mode==="server-detail"){
      state.mode="403";eq((await f.api()).status,403);
      ok(f.notice().textContent.includes("raw-403-detail"),"original server detail kept");
      const before=f.preserve();await f.switchTo("en");eq(f.preserve(),before);ok(f.notice().textContent.includes("raw-403-detail"));
    } else if(mode==="local-file"){
      const count=state.calls.length,r=await f.api("/api/open");eq(r.status,405);
      eq(r.value.detail,data.catalog.locales.ja[PREFIX+"local_file_required"]);eq(state.calls.length,count);
    } else if(["late-response","late-body","late-401-body","late-ready","late-activation"].includes(mode)){
      if(mode==="late-ready"){
        const d=deferred();windows.OpenCADLocale.ready=d.promise;state.locale="zh-CN";await f.api();
        state.mode="401";await f.api();const count=state.updates.length;d.resolve();await settle();eq(state.updates.length,count);
      } else if(mode==="late-activation"){
        // A public copy update is not an authority; malformed project state is refused.
        state.project="p-foreign";const before=state.updates.length;await f.api();await settle();
        eq(state.updates.length,before,"foreign project language ignored");
      } else {
        if(mode==="late-response")state.delayPath="/api/state";else state.bodyPath="/api/state";
        if(mode==="late-401-body")state.mode="401-body";
        const old=f.api();await settle();eq(state.delays.length,1,"response actually deferred");
        state.delayPath=null;state.bodyPath=null;state.mode="401";
        if(mode==="late-401-body"){state.mode="logout-failed";await f.bar().querySelectorAll("button")[3].click();}
        else await f.api();
        const before=state.updates.length;state.delays.shift().resolve();eq((await old).error,"session_boundary_changed");
        await settle();eq(state.updates.length,before);
      }
    } else if(mode==="read-shield"||mode==="damaged-shield"){
      state.mode=mode==="read-shield"?"403":"damaged";await f.api();
      eq(doc.body.dataset.opencadSessionLocked,"true");eq(workspace.style.visibility,"hidden");eq(workspace.inert,true);
      const before=f.preserve();await f.switchTo("en");eq(f.preserve(),before);
    } else if(mode==="public-alert-contract"){
      state.mode="403";await f.api("/api/state");
      eq(doc.body.dataset.opencadSessionLocked,"true","contract requires locked workspace");
      eq(workspace.style.visibility,"hidden","workspace hidden before public alerts");
      function makeAlert(owner,code,text=code){
        const node=doc.createElement("output");
        node.setAttribute("role","alert");node.setAttribute("lang","en");
        node.setAttribute("data-opencad-public-locale-error",owner);
        node.textContent=text;
        return node;
      }
      const safe=[];
      for(const [owner,code] of [
        ["foundation","locale_package_http_error"],
        ["foundation","locale_package_load_failed"],
        ["foundation","locale_unsupported"],
        ["parametric","parametric_locale_initialization_failed"],
        ["private","private_locale_initialization_failed"]
      ]){
        const node=makeAlert(owner,code);doc.body.append(node);await settle();
        ok(node.parentNode===doc.body&&node.style.visibility!=="hidden"&&node.inert!==true,
          "locked-safe public locale alert hidden: "+owner+"/"+code);
        safe.push(node);
      }
      {
        const unrelated=doc.createElement("span");doc.body.append(unrelated);await settle();
        ok(unrelated.style.visibility==="hidden"&&unrelated.inert===true,
          "repeated shield did not cover unrelated new child");
        for(const node of safe){ok(node.style.visibility!=="hidden"&&node.inert!==true,
          "repeated shield hid a live-safe public locale alert");}
      }
      const negatives=[];
      const wrongRole=makeAlert("parametric","parametric_locale_initialization_failed");
      wrongRole.setAttribute("role","status");negatives.push(wrongRole);
      const extraAttribute=makeAlert("private","private_locale_initialization_failed");
      extraAttribute.setAttribute("title","private-title");negatives.push(extraAttribute);
      negatives.push(makeAlert("foundation","locale_package_http_error private-token raw-403-detail"));
      const withChild=makeAlert("private","private_locale_initialization_failed");
      withChild.append(doc.createElement("span"));negatives.push(withChild);
      negatives.push(makeAlert("private","locale_package_http_error"));
      const wrongLang=makeAlert("private","private_locale_initialization_failed");
      wrongLang.setAttribute("lang","zh-CN");negatives.push(wrongLang);
      negatives.push(makeAlert("session","private_locale_initialization_failed"));
      for(const node of negatives){
        doc.body.append(node);await settle();
        ok(node.style.visibility==="hidden"&&node.inert===true,
          "nonconforming public locale alert was exempted from shield");
      }
      const nestedRoot=doc.createElement("section");
      nestedRoot.append(makeAlert("parametric","parametric_locale_initialization_failed"));
      doc.body.append(nestedRoot);await settle();
      ok(nestedRoot.style.visibility==="hidden"&&nestedRoot.inert===true,
        "non-body-direct public locale diagnostic ancestor was not shielded");
      const before=f.preserve();await f.switchTo("en");eq(f.preserve(),before,
        "public alert handling changed session state");
      for(const node of safe){ok(node.style.visibility!=="hidden"&&node.inert!==true,
        "later shield calls hid a live-safe public locale alert");}
    } else if(mode==="binding-release"){
      for(const code of ["en","ja","zh-CN","en"])await f.switchTo(code);
      state.mode="offline";await f.api("/api/save",{});
      await f.switchTo("ja");eq(f.notice().getAttribute("data-panel-i18n"),PREFIX+"connection_pending");
      ok(!f.nodes().some(n=>n.hasAttribute("data-panel-i18n")&&n.children.length),"leaf bindings");
    } else if(mode==="project-title"){
      // All options remain data, never a translation binding.
      state.auth=true;eq(draft.value,"draft-original");
      ok(!f.nodes().some(n=>n.tagName==="OPTION"&&n.hasAttribute("data-panel-i18n")));
    } else if(mode==="logout-detail"){
      state.mode="logout-failed";await f.bar().querySelectorAll("button")[3].click();await settle();
      ok(f.notice().textContent.includes("raw-logout-detail"));eq(doc.body.dataset.opencadSessionLocked,"true");
      const before=f.preserve();await f.switchTo("en");eq(f.preserve(),before);
      ok(f.notice().textContent.includes("raw-logout-detail"));
    } else if(mode==="account-storage"){
      eq(JSON.parse(f.storage.get("opencad-project-id")).owner_id,"alice");
      eq(state.updates[0].locale,"ja","stored other-owner locale not inherited");
      eq(f.storage.get("opencad-locale"),"zh-CN","no locale storage write");
    } else if(mode==="locale-response"){
      state.locale="en";eq((await f.api("/api/locale",{locale:"en"})).status,200);await settle();
      eq(state.updates.at(-1).locale,"en");eq(state.calls.at(-1).body,{locale:"en"},"locale is not CAS mutation");
    } else throw Error("unknown_case:"+mode);
  }
  ok(checks>0,"nonempty checks");
  complete=true;
  console.log(JSON.stringify({mode,verdict:"PASS",checks,source_sha256:require("crypto").createHash("sha256")
    .update(fs.readFileSync(root+"/oss-alternatives/apps/opencad/web/js/project-session.js")).digest("hex")}));
}
run().catch(e=>{complete=true;console.error(JSON.stringify({mode,verdict:"FAIL",checks,first_cause:e.message,stack:e.stack}));process.exitCode=1;});
"""


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(value, code):
    if not value:
        raise AssertionError(code)


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    require(result.returncode == 0, "git_failed:" + " ".join(args))
    return result.stdout


def source(root, development):
    """Bind HEAD and every physical source byte/mode; never a frozen file census."""
    head = git(root, "rev-parse", "HEAD").decode().strip()
    git(root, "merge-base", "--is-ancestor", BASE, head)
    status = git(root, "status", "--porcelain=v1", "--untracked-files=all").decode().splitlines()
    require(not status or development, "source_not_clean")
    if development:
        require(all(row[3:] in OWNED for row in status), "development_scope_mismatch")
    committed = {}
    rows = git(root, "ls-tree", "-rz", head)
    ids = []
    for row in rows.split(b"\0"):
        if not row:
            continue
        meta, name = row.split(b"\t")
        mode, kind, oid = meta.decode().split()
        require(kind == "blob" and mode in {"100644", "100755"}, "nonregular_git_source")
        ids.append((name.decode(), mode, oid))
    batch = subprocess.run(["git", "-C", str(root), "cat-file", "--batch"],
                           input="".join(oid + "\n" for _, _, oid in ids).encode(),
                           capture_output=True, check=True).stdout
    cursor = 0
    for name, mode, oid in ids:
        end = batch.index(b"\n", cursor)
        returned, kind, length = batch[cursor:end].decode().split()
        require(returned == oid and kind == "blob", "git_blob_binding")
        value = batch[end + 1:end + 1 + int(length)]
        committed[name] = {"bytes": len(value), "sha256": digest(value), "mode": mode}
        cursor = end + int(length) + 2
    require(cursor == len(batch), "git_blob_trailing_bytes")
    files = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        if ".git" in dirs:
            require(Path(directory) == root, "nested_git_metadata")
            dirs.remove(".git")
        require(all(not (Path(directory) / d).is_symlink() for d in dirs),
                "linked_source_directory")
        for name in names:
            path = Path(directory) / name
            if name == ".git":
                require(Path(directory) == root and not path.is_symlink(), "nested_git_metadata")
                continue
            require(path.is_file() and not path.is_symlink(), "nonregular_physical_source")
            value = path.read_bytes()
            files[path.relative_to(root).as_posix()] = {
                "bytes": len(value), "sha256": digest(value),
                "mode": "100755" if path.stat().st_mode & stat.S_IXUSR else "100644"}
    delta = sorted(n for n in files.keys() | committed.keys() if files.get(n) != committed.get(n))
    require(not delta or (development and set(delta) <= OWNED), "source_byte_mode_or_scope")
    require(OWNED <= files.keys(), "owned_source_missing")
    return {"head": head, "status": status, "development": development,
            "files": files, "sha256": digest(json.dumps(files, sort_keys=True).encode())}


def foundation_binding(path):
    head = git(path, "rev-parse", "HEAD").decode().strip()
    git(path, "merge-base", "--is-ancestor", FOUNDATION_HEAD, head)
    require(not git(path, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "foundation_not_clean")
    data = (path / LOCALE).read_bytes()
    require(data == git(path, "show", head + ":" + LOCALE),
            "foundation_source_changed")
    return {"head": head, "path": str(path), "sha256": digest(data), "bytes": len(data)}


def catalog_binding(path):
    head = git(path, "rev-parse", "HEAD").decode().strip()
    require(not git(path, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "catalog_not_clean")
    data = (path / CATALOG).read_bytes()
    require(data == git(path, "show", head + ":" + CATALOG), "catalog_source_changed")
    return {"head": head, "path": str(path), "sha256": digest(data), "bytes": len(data)}


def request_keymap():
    lines = (ROOT / DOC).read_text().splitlines()
    start = lines.index("| Key Suffix | zh-CN | en | ja |")
    packs = {code: {} for code in ("zh-CN", "en", "ja")}
    for row in lines[start + 2:]:
        if not row.startswith("|"):
            break
        cells = re.split(r"(?<!\\)\|", row)[1:-1]
        require(len(cells) == 4, "keymap_four_columns")
        suffix, *values = [v.strip().replace(r"\|", "|") for v in cells]
        require(re.fullmatch("[a-z_]+", suffix), "keymap_suffix")
        for code, text in zip(packs, values):
            key = "panel.session." + suffix
            require(key not in packs[code] and text, "keymap_duplicate_or_empty")
            packs[code][key] = text
    require(len(packs["en"]) >= 49, "keymap_empty_or_incomplete")
    return packs


def placeholder_set(text):
    require(isinstance(text, str) and text.strip(), "catalog_empty_translation")
    require(not re.search(r"[{}]", PLACEHOLDER.sub("", text)), "catalog_invalid_placeholder")
    return set(PLACEHOLDER.findall(text))


def keymap(catalog_root):
    request = request_keymap()
    payload = strict_json(catalog_root / CATALOG)
    require(isinstance(payload, dict) and set(payload) == {"schema", "default_locale", "locales"} and
            payload.get("schema") == "opencad.panel-i18n.v1" and
            payload.get("default_locale") == "en", "catalog_schema_or_locales")
    locales = payload.get("locales")
    require(isinstance(locales, dict) and set(locales) == {"en", "ja", "zh-CN"},
            "catalog_schema_or_locales")
    packs = {}
    for code, pack in locales.items():
        require(isinstance(pack, dict), "catalog_schema_or_locales")
        actual = {k: v for k, v in pack.items() if k.startswith("panel.session.")}
        expected = set(request[code])
        have = set(actual)
        missing, extra = expected - have, have - expected
        require(not missing and not extra, "actual_session_keys:" + code +
                ":missing=" + ",".join(sorted(missing)[:8]) +
                ":extra=" + ",".join(sorted(extra)[:8]))
        for key, text in actual.items():
            require(placeholder_set(text) == placeholder_set(request[code][key]),
                    "actual_session_placeholders:" + code + ":" + key)
        packs[code] = actual
    require(len(packs["en"]) >= 49, "catalog_session_empty")
    return {"schema": "opencad.panel-i18n.v1", "default_locale": "en", "locales": packs}


def strict_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=unique)


def artifacts(out):
    result = {}
    for path in sorted(out.rglob("*")):
        require(not path.is_symlink(), "linked_evidence")
        if path.is_file() and path != out / "result.json":
            value = path.read_bytes()
            result[path.relative_to(out).as_posix()] = {"bytes": len(value), "sha256": digest(value)}
    return result


def execute(root, foundation, catalog, out, cases):
    fixture = out / "session-fixture.cjs"
    fixture.write_text(NODE)
    data = out / "fixture-data.json"
    data.write_text(json.dumps({"catalog": keymap(catalog)}, ensure_ascii=True) + "\n")
    records = []
    for case in cases:
        argv = [shutil.which("node") or "node", str(fixture), str(root), str(foundation), str(data), case]
        at = stamp()
        child = subprocess.run(argv, cwd=root, capture_output=True)
        (out / (case + ".stdout.log")).write_bytes(child.stdout)
        (out / (case + ".stderr.log")).write_bytes(child.stderr)
        stream = child.stdout if child.returncode == 0 else child.stderr
        try:
            terminal = json.loads(stream.decode().splitlines()[-1])
        except (ValueError, IndexError, UnicodeError):
            terminal = {"mode": case, "verdict": "FAIL", "checks": None,
                        "first_cause": "child_terminal_missing"}
        records.append({"mode": case, "argv": argv, "actual_rc": child.returncode,
                        "started_at": at, "finished_at": stamp(), "terminal": terminal})
    return records


def verify(report, root, evidence, parent_rc, foundation, catalog):
    require(parent_rc == 0, "parent_rc_not_zero")
    require(report["verdict"] == "PASS" and report["actual_rc"] == 0
            and report["first_cause"] is None, "producer_not_pass")
    require(report["source_root"] == str(root), "source_root_mismatch")
    require(report["source_before"] == report["source_after"]
            == source(root, report["source_before"]["development"]), "current_source_drift")
    require(report["foundation"] == foundation_binding(foundation), "foundation_drift")
    require(report["catalog"] == catalog_binding(catalog), "catalog_drift")
    require(report["artifacts"] == artifacts(evidence), "artifact_closure_mismatch")
    require((evidence / "session-fixture.cjs").read_text() == NODE, "fixture_source_drift")
    require(strict_json(evidence / "fixture-data.json") == {"catalog": keymap(catalog)}, "keymap_drift")
    records = report["children"]
    require([r["mode"] for r in records] == list(CASES), "child_set_mismatch")
    total = 0
    for row in records:
        name = row["mode"]
        expected = [shutil.which("node") or "node", str(evidence / "session-fixture.cjs"),
                    str(root), str(foundation), str(evidence / "fixture-data.json"), name]
        require(row["argv"] == expected, "child_argv_mismatch")
        require(row["actual_rc"] == 0, "child_rc_mismatch")
        terminal = strict_json(evidence / (name + ".stdout.log"))
        require(terminal == row["terminal"] and terminal["verdict"] == "PASS"
                and isinstance(terminal["checks"], int) and terminal["checks"] > 0,
                "child_terminal_mismatch")
        require(not (evidence / (name + ".stderr.log")).read_bytes(), "child_stderr_present")
        require(terminal["source_sha256"] == report["source_after"]["files"][SCRIPT]["sha256"],
                "child_product_source_mismatch")
        require(row["started_at"] <= row["finished_at"], "child_timestamp_order")
        total += terminal["checks"]
    require(report["tests"] == len(CASES) and report["checks"] == total, "count_mismatch")
    marker = f"{MARKER} verdict=PASS tests={len(CASES)} checks={total} rc=0\n"
    require((evidence / "completion.log").read_text() == marker, "completion_marker_mismatch")
    return total


def reader_controls(prior, root, foundation, catalog, out):
    report = strict_json(prior)
    verify(report, root, prior.parent, 0, foundation, catalog)
    mutations = {
        "parent-rc": "parent_rc_not_zero", "child-missing": "child_set_mismatch",
        "child-rc": "child_rc_mismatch", "counts": "count_mismatch",
        "source-sha": "current_source_drift", "source-mode": "current_source_drift",
        "foreign-root": "source_root_mismatch", "artifact-corrupt": "artifact_closure_mismatch",
        "extra-artifact": "artifact_closure_mismatch", "rehashed-terminal": "child_terminal_mismatch",
        "completion": "completion_marker_mismatch", "empty-source": "current_source_drift",
    }
    records = []
    for name, expected in mutations.items():
        case = out / name
        case.mkdir()
        evidence = case / "evidence"
        shutil.copytree(prior.parent, evidence)
        changed = copy.deepcopy(report)
        if name == "child-missing":
            changed["children"].pop()
        elif name == "child-rc":
            changed["children"][0]["actual_rc"] = 1
        elif name == "counts":
            changed["tests"] += 1
        elif name in {"source-sha", "source-mode", "empty-source"}:
            if name == "source-sha":
                changed["source_before"]["sha256"] = "0" * 64
            elif name == "source-mode":
                changed["source_before"]["files"][SCRIPT]["mode"] = "100755"
            else:
                changed["source_before"]["files"] = {}
            changed["source_after"] = copy.deepcopy(changed["source_before"])
        elif name == "foreign-root":
            changed["source_root"] = "/data/tmp/foreign"
        elif name == "artifact-corrupt":
            (evidence / "anonymous.stdout.log").write_text("corrupt\n")
        elif name == "extra-artifact":
            (evidence / "unexpected.log").write_text("unbound\n")
        elif name == "rehashed-terminal":
            terminal = copy.deepcopy(changed["children"][0]["terminal"])
            terminal["checks"] += 1
            (evidence / "anonymous.stdout.log").write_text(json.dumps(terminal) + "\n")
        elif name == "completion":
            (evidence / "completion.log").write_text("PASS\n")
        for row in changed["children"]:
            row["argv"][1] = str(evidence / "session-fixture.cjs")
            row["argv"][4] = str(evidence / "fixture-data.json")
        if name in {"rehashed-terminal", "completion"}:
            changed["artifacts"] = artifacts(evidence)
        (evidence / "result.json").write_text(json.dumps(changed, indent=2) + "\n")
        argv = [sys.executable, "-B", str(root / TEST), "--verify", str(evidence / "result.json"),
                "--source-root", str(root), "--foundation-root", str(foundation),
                "--catalog-root", str(catalog),
                "--parent-rc", "1" if name == "parent-rc" else "0", "--out", str(case / "reader")]
        child = subprocess.run(argv, capture_output=True)
        (case / "reader.stdout.log").write_bytes(child.stdout)
        (case / "reader.stderr.log").write_bytes(child.stderr)
        terminal = strict_json(case / "reader/result.json")
        records.append({"mode": name, "actual_rc": child.returncode, "argv": argv,
                        "expected": expected, "terminal": terminal, "at": stamp()})
        require(child.returncode == 1 and terminal["first_cause"] == expected,
                "reader_control_mismatch:" + name)
    return records


class ProjectSessionLocaleTests(unittest.TestCase):
    def test_real_node_product(self):
        evidence = os.environ.get("OPENCAD_EVIDENCE_ROOT")
        require(evidence, "OPENCAD_EVIDENCE_ROOT_required")
        out = Path(evidence) / "project-session-locale-r2"
        out.mkdir(parents=True, exist_ok=False)
        records = execute(ROOT, FOUNDATION, CATALOG_HOME, out, CASES)
        (out / "children.json").write_text(json.dumps(records, indent=2) + "\n")
        self.assertEqual([r["actual_rc"] for r in records], [0] * len(CASES))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--selftest", action="store_true")
    group.add_argument("--verify", type=Path)
    group.add_argument("--controls", type=Path)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--foundation-root", type=Path, default=FOUNDATION)
    parser.add_argument("--catalog-root", type=Path, default=CATALOG_HOME)
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--parent-rc", type=int)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path)
    args = parser.parse_args()
    import pwd
    require(os.geteuid() == pwd.getpwnam("infra").pw_uid, "infra_identity_required")
    root, foundation, catalog, out = (args.source_root.absolute(), args.foundation_root.absolute(),
                                      args.catalog_root.absolute(), args.out.absolute())
    require(out.resolve().is_relative_to(Path("/hf3fs-jd/data/tmp").resolve())
            and not out.exists(), "fresh_external_out_required")
    out.mkdir(parents=True, exist_ok=False)
    result = {"verdict": "FAIL", "actual_rc": 1, "first_cause": None,
              "source_root": str(root), "started_at": stamp(), "children": []}
    try:
        if args.verify:
            require(args.parent_rc is not None, "actual_parent_rc_required")
            prior = args.verify.absolute()
            require(not out.resolve().is_relative_to(prior.resolve(strict=True).parent),
                    "reader_out_overlap")
            frozen = prior.read_bytes()
            result["input_sha256"] = digest(frozen)
            result["checks"] = verify(strict_json(prior), root, prior.parent,
                                      args.parent_rc, foundation, catalog)
            require(prior.read_bytes() == frozen, "original_evidence_changed")
            result["tests"] = len(CASES)
        elif args.controls:
            require(args.parent_rc == 0, "actual_parent_rc_required")
            result["children"] = reader_controls(args.controls, root, foundation, catalog, out)
            result["tests"] = len(result["children"])
        else:
            require(args.runtime_root is not None, "runtime_root_required")
            runtime = args.runtime_root.absolute()
            require(runtime.is_relative_to(Path("/data/tmp")) and not runtime.exists()
                    and not runtime.is_symlink(), "fresh_local_runtime_required")
            runtime.mkdir(parents=True, exist_ok=False)
            for name in ("TMPDIR", "TMP", "TEMP"):
                os.environ[name] = str(runtime)
            result["runtime"] = str(runtime)
            result["source_before"] = source(root, args.development)
            result["foundation"] = foundation_binding(foundation)
            result["catalog"] = catalog_binding(catalog)
            cases = (args.case,) if args.case else CASES
            result["children"] = execute(root, foundation, catalog, out, cases)
            result["tests"] = len(result["children"])
            result["checks"] = sum(r["terminal"].get("checks") or 0 for r in result["children"])
            result["source_after"] = source(root, args.development)
            require(result["source_before"] == result["source_after"], "source_drift")
            failed = [r for r in result["children"] if r["actual_rc"] != 0 or
                      r["terminal"].get("verdict") != "PASS" or not r["terminal"].get("checks")]
            require(not failed, "child_failed:" + (failed[0]["mode"] + ":" +
                    str(failed[0]["terminal"].get("first_cause")) if failed else ""))
        result.update(verdict="PASS", actual_rc=0)
    except Exception as failure:
        result["first_cause"] = str(failure)
    result["finished_at"] = stamp()
    marker = (f"{MARKER} verdict={result['verdict']} tests={result.get('tests', 'NA')} "
              f"checks={result.get('checks', 'NA')} rc={result['actual_rc']}")
    (out / "completion.log").write_text(marker + "\n")
    result["artifacts"] = artifacts(out)
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(marker + " at=" + result["finished_at"] + " out=" + str(out), flush=True)
    if result["first_cause"]:
        print("first_cause=" + result["first_cause"], flush=True)
    return result["actual_rc"]


if __name__ == "__main__":
    raise SystemExit(main())
