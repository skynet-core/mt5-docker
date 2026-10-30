#property strict
#property version "1.00"
#property description "Offline chart input regression; never submits trades."

int log_file=INVALID_HANDLE;
string names[]={"ChartInput-red","ChartInput-blue"};
int positions[]={40,210};
color colors[]={C'203,41,97',C'43,83,211'};

int OnInit()
{
   log_file=FileOpen("chart-input.csv",FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_SHARE_READ,';',CP_UTF8);
   if(log_file==INVALID_HANDLE)
      return INIT_FAILED;
   FileWrite(log_file,"event","name","x","y");
   for(int i=0;i<ArraySize(names);i++)
   {
      if(!ObjectCreate(0,names[i],OBJ_BUTTON,0,0,0) ||
         !ObjectSetInteger(0,names[i],OBJPROP_CORNER,CORNER_LEFT_UPPER) ||
         !ObjectSetInteger(0,names[i],OBJPROP_XDISTANCE,positions[i]) ||
         !ObjectSetInteger(0,names[i],OBJPROP_YDISTANCE,40) ||
         !ObjectSetInteger(0,names[i],OBJPROP_XSIZE,120) ||
         !ObjectSetInteger(0,names[i],OBJPROP_YSIZE,44) ||
         !ObjectSetInteger(0,names[i],OBJPROP_BGCOLOR,colors[i]) ||
         !ObjectSetInteger(0,names[i],OBJPROP_COLOR,clrWhite) ||
         !ObjectSetInteger(0,names[i],OBJPROP_SELECTABLE,false) ||
         !ObjectSetInteger(0,names[i],OBJPROP_ZORDER,100) ||
         !ObjectSetString(0,names[i],OBJPROP_TEXT,names[i]))
         return INIT_FAILED;
   }
   ChartRedraw();
   FileWrite(log_file,"READY","",0,0);
   FileFlush(log_file);
   return INIT_SUCCEEDED;
}

void OnTick() {}

void OnChartEvent(const int id,const long &lparam,const double &dparam,const string &sparam)
{
   if(id!=CHARTEVENT_OBJECT_CLICK || log_file==INVALID_HANDLE)
      return;
   FileWrite(log_file,"OBJECT_CLICK",sparam,lparam,dparam);
   FileFlush(log_file);
}

void OnDeinit(const int reason)
{
   if(log_file!=INVALID_HANDLE)
      FileClose(log_file);
   for(int i=0;i<ArraySize(names);i++)
      ObjectDelete(0,names[i]);
}
