//+------------------------------------------------------------------+
//|                                           AlgoSamurai_Fixed.mq5   |
//| Revised MT5 port of Algo Samurai                                 |
//| Fixes: input/event syntax, TP preservation, trailing logic,       |
//| broker price/stop validation, result-code checks and risk sizing. |
//+------------------------------------------------------------------+
#property copyright "AlgoSamurai (revised MT5 port)"
#property link      "http://ea.kitgain.com/post-484.html"
#property version   "1.10"
#property description "Buy-stop/sell-stop EA with trailing SL and validated risk sizing"

#include <Trade\Trade.mqh>

//+------------------------------------------------------------------+
//| Inputs                                                           |
//+------------------------------------------------------------------+
input group "=== General ==="
input long   InpMagicNumber          = 777;  // Magic number
input bool   InpAutoLot              = true; // Size by monetary risk
input double InpRiskPercent          = 1.0;  // Equity risk per trade (%) when AutoLot=true
input double InpFixLot               = 0.1;  // Fixed lot when AutoLot=false
input bool   InpOnePositionAtATime   = true; // Cancel pending orders while a position is open

input group "=== Order (points) ==="
input int    InpStopLoss             = 150;  // Stop loss in points; 0 disables SL
input int    InpTakeProfit           = 1250; // Take profit in points; 0 disables TP
input int    InpStep                 = 90;   // Pending-entry offset in points
input int    InpSlippage             = 10;   // Maximum deviation in points

input group "=== Time filter (broker server time) ==="
input int    InpStartHour            = 4;  // Start hour (0-23)
input int    InpStopHour             = 22; // Stop hour (0-23); equal start/stop means all day

//+------------------------------------------------------------------+
//| Trailing constants                                               |
//+------------------------------------------------------------------+
const int TRAIL_PROFIT_STEP = 200; // Step while position is profitable (points)
const int TRAIL_PROFIT_GAP  = 250; // Minimum market-to-SL gap while profitable (points)
const int TRAIL_FLAT_STEP   = 50;  // Step while position is not profitable (points)
const int TRAIL_FLAT_GAP    = 100; // Minimum market-to-SL gap while not profitable (points)

//+------------------------------------------------------------------+
//| Globals                                                          |
//+------------------------------------------------------------------+
CTrade trade;
int LotDigits       = 2;
int stopsLevelPts   = 0;
int freezeLevelPts  = 0;
datetime g_lastBuyStopSet  = 0;
datetime g_lastSellStopSet = 0;
datetime g_lastLotWarning  = 0;

//+------------------------------------------------------------------+
//| Quote helpers                                                    |
//+------------------------------------------------------------------+
double SymbolBid()
{
   return SymbolInfoDouble(_Symbol, SYMBOL_BID);
}

double SymbolAsk()
{
   return SymbolInfoDouble(_Symbol, SYMBOL_ASK);
}

double SymbolTickSize()
{
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickSize <= 0.0)
      tickSize = _Point;
   return tickSize;
}

double RoundPriceDown(const double price)
{
   double tickSize = SymbolTickSize();
   double rounded  = MathFloor(price / tickSize + 1e-10) * tickSize;
   return NormalizeDouble(rounded, _Digits);
}

double RoundPriceUp(const double price)
{
   double tickSize = SymbolTickSize();
   double rounded  = MathCeil(price / tickSize - 1e-10) * tickSize;
   return NormalizeDouble(rounded, _Digits);
}

int EntryOffsetPoints()
{
   // Keep pending entries outside the broker's published stop distance.
   return (int)MathMax(1, MathMax(InpStep, stopsLevelPts));
}

int EffectiveDistancePoints(const int requestedPoints)
{
   if(requestedPoints <= 0)
      return 0; // Zero intentionally disables this SL/TP.
   return (int)MathMax(requestedPoints, stopsLevelPts);
}

//+------------------------------------------------------------------+
//| Trade result helper                                              |
//+------------------------------------------------------------------+
bool CheckTradeResult(const bool methodResult,
                      const string action,
                      const bool allowPlaced = false)
{
   uint retcode = trade.ResultRetcode();
   bool accepted = (retcode == TRADE_RETCODE_DONE ||
                    retcode == TRADE_RETCODE_NO_CHANGES ||
                    (allowPlaced && retcode == TRADE_RETCODE_PLACED));

   if(!methodResult || !accepted)
   {
      Print("AlgoSamurai: ", action, " failed. Retcode=", retcode,
            " (", trade.ResultRetcodeDescription(), ")");
      return false;
   }
   return true;
}

void LotWarning(const string message)
{
   datetime now = TimeCurrent();
   if(g_lastLotWarning == 0 || now - g_lastLotWarning >= 60)
   {
      Print("AlgoSamurai: ", message);
      g_lastLotWarning = now;
   }
}

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   if(InpMagicNumber < 0)
   {
      Print("AlgoSamurai: Magic number must be non-negative.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpStep < 0 || InpStopLoss < 0 || InpTakeProfit < 0 || InpSlippage < 0)
   {
      Print("AlgoSamurai: Step, SL, TP and slippage inputs cannot be negative.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpStartHour < 0 || InpStartHour > 23 ||
      InpStopHour < 0 || InpStopHour > 23)
   {
      Print("AlgoSamurai: Trading hours must be between 0 and 23.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpAutoLot && (InpRiskPercent <= 0.0 || InpStopLoss == 0))
   {
      Print("AlgoSamurai: AutoLot requires RiskPercent > 0 and an enabled StopLoss.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(!InpAutoLot && InpFixLot <= 0.0)
   {
      Print("AlgoSamurai: Fixed lot must be greater than zero.");
      return INIT_PARAMETERS_INCORRECT;
   }

   stopsLevelPts  = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   freezeLevelPts = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   LotDigits      = CalcLotDigits();

   trade.SetExpertMagicNumber((ulong)InpMagicNumber);
   trade.SetDeviationInPoints((ulong)InpSlippage);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetAsyncMode(false);

   g_lastBuyStopSet  = 0;
   g_lastSellStopSet = 0;
   g_lastLotWarning  = 0;
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   ObjectsDeleteAll(0, "AS_");
}

//+------------------------------------------------------------------+
//| Expert tick                                                      |
//+------------------------------------------------------------------+
void OnTick()
{
   stopsLevelPts  = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   freezeLevelPts = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);

   Display_Info();

   int buyCount  = 0;
   int sellCount = 0;

   // 1) Manage open positions and count positions for this EA/symbol.
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber)
         continue;

      ENUM_POSITION_TYPE ptype =
         (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double sl   = PositionGetDouble(POSITION_SL);
      double tp   = PositionGetDouble(POSITION_TP);
      double open = PositionGetDouble(POSITION_PRICE_OPEN);

      if(ptype == POSITION_TYPE_BUY)
      {
         buyCount++;
         TrailPosition(ticket, ptype, open, sl, tp);
      }
      else if(ptype == POSITION_TYPE_SELL)
      {
         sellCount++;
         TrailPosition(ticket, ptype, open, sl, tp);
      }
   }

   int openPositionCount = buyCount + sellCount;
   bool inWindow = dTime();

   // 2) Find our BUY STOP / SELL STOP orders. Keep only one of each;
   //    delete extras to avoid unmanaged duplicate orders.
   ulong buyStopTicket  = 0;
   ulong sellStopTicket = 0;
   double buyStopPrice  = 0.0;
   double sellStopPrice = 0.0;

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0)
         continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol)
         continue;
      if(OrderGetInteger(ORDER_MAGIC) != InpMagicNumber)
         continue;

      ENUM_ORDER_TYPE otype = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      double orderPrice = OrderGetDouble(ORDER_PRICE_OPEN);

      if(otype == ORDER_TYPE_BUY_STOP)
      {
         if(buyStopTicket != 0)
         {
            CheckTradeResult(trade.OrderDelete(ticket), "delete duplicate BUY STOP");
            continue;
         }
         buyStopTicket = ticket;
         buyStopPrice  = orderPrice;
      }
      else if(otype == ORDER_TYPE_SELL_STOP)
      {
         if(sellStopTicket != 0)
         {
            CheckTradeResult(trade.OrderDelete(ticket), "delete duplicate SELL STOP");
            continue;
         }
         sellStopTicket = ticket;
         sellStopPrice  = orderPrice;
      }
   }

   // 3) Remove pendings outside the session. By default, also remove all
   //    pending entries while any position is open (one-position mode).
   bool clearPendings = (!inWindow ||
                         (InpOnePositionAtATime && openPositionCount > 0));
   if(clearPendings)
   {
      if(buyStopTicket != 0 &&
         CheckTradeResult(trade.OrderDelete(buyStopTicket), "delete BUY STOP"))
      {
         buyStopTicket = 0;
         buyStopPrice  = 0.0;
      }
      if(sellStopTicket != 0 &&
         CheckTradeResult(trade.OrderDelete(sellStopTicket), "delete SELL STOP"))
      {
         sellStopTicket = 0;
         sellStopPrice  = 0.0;
      }
   }

   // 4) Place new stop orders. Each side uses SL-aware volume sizing.
   bool mayPlace = inWindow &&
                   (!InpOnePositionAtATime || openPositionCount == 0);

   if(mayPlace && buyCount == 0 && buyStopTicket == 0)
   {
      double price, sl, tp;
      if(CalcBuyStop(price, sl, tp))
      {
         double lots = CalcLotsForOrder(ORDER_TYPE_BUY, price, sl);
         if(lots > 0.0)
         {
            bool methodOK = trade.BuyStop(lots, price, _Symbol, sl, tp,
                                          ORDER_TIME_GTC, 0, "AlgoSamurai");
            if(CheckTradeResult(methodOK, "place BUY STOP", true))
               g_lastBuyStopSet = TimeCurrent();
         }
      }
   }

   if(mayPlace && sellCount == 0 && sellStopTicket == 0)
   {
      double price, sl, tp;
      if(CalcSellStop(price, sl, tp))
      {
         double lots = CalcLotsForOrder(ORDER_TYPE_SELL, price, sl);
         if(lots > 0.0)
         {
            bool methodOK = trade.SellStop(lots, price, _Symbol, sl, tp,
                                           ORDER_TIME_GTC, 0, "AlgoSamurai");
            if(CheckTradeResult(methodOK, "place SELL STOP", true))
               g_lastSellStopSet = TimeCurrent();
         }
      }
   }

   // 5) Re-anchor pending orders. The same validated price builders are
   //    used for initial entry and modification, avoiding point/tick drift.
   if(mayPlace && buyStopTicket != 0 &&
      g_lastBuyStopSet < TimeCurrent() - 5)
   {
      double price, sl, tp;
      if(CalcBuyStop(price, sl, tp) &&
         MathAbs(price - buyStopPrice) / _Point > 30.0)
      {
         bool methodOK = trade.OrderModify(buyStopTicket, price, sl, tp,
                                           ORDER_TIME_GTC, 0, 0.0);
         if(CheckTradeResult(methodOK, "modify BUY STOP"))
            g_lastBuyStopSet = TimeCurrent();
      }
   }

   if(mayPlace && sellStopTicket != 0 &&
      g_lastSellStopSet < TimeCurrent() - 5)
   {
      double price, sl, tp;
      if(CalcSellStop(price, sl, tp) &&
         MathAbs(price - sellStopPrice) / _Point > 30.0)
      {
         bool methodOK = trade.OrderModify(sellStopTicket, price, sl, tp,
                                           ORDER_TIME_GTC, 0, 0.0);
         if(CheckTradeResult(methodOK, "modify SELL STOP"))
            g_lastSellStopSet = TimeCurrent();
      }
   }
}

//+------------------------------------------------------------------+
//| Trailing logic                                                   |
//+------------------------------------------------------------------+
void TrailPosition(const ulong ticket,
                   const ENUM_POSITION_TYPE ptype,
                   const double openPrice,
                   const double currentSL,
                   const double currentTP)
{
   // Without a current SL there is no safe step reference. Do not build a
   // candidate from zero; that can create a nonsensically distant SL.
   if(currentSL <= 0.0)
      return;

   double bid = SymbolBid();
   double ask = SymbolAsk();
   if(bid <= 0.0 || ask <= 0.0)
      return;

   int brokerGapPts = (int)MathMax(stopsLevelPts, freezeLevelPts);

   if(ptype == POSITION_TYPE_BUY)
   {
      bool profitable = (bid > openPrice);
      int stepPts = profitable ? TRAIL_PROFIT_STEP : TRAIL_FLAT_STEP;
      int gapPts  = profitable ? TRAIL_PROFIT_GAP  : TRAIL_FLAT_GAP;

      double candidate = RoundPriceDown(currentSL + stepPts * _Point);
      double gapLimit  = RoundPriceDown(bid - gapPts * _Point);
      double stopLimit = RoundPriceDown(bid - brokerGapPts * _Point);
      double maxAllowed = MathMin(gapLimit, stopLimit);

      if(candidate > currentSL && candidate <= maxAllowed + (_Point * 1e-6))
      {
         bool methodOK = trade.PositionModify(ticket, candidate, currentTP);
         CheckTradeResult(methodOK, "modify BUY SL");
      }
   }
   else if(ptype == POSITION_TYPE_SELL)
   {
      bool profitable = (ask < openPrice);
      int stepPts = profitable ? TRAIL_PROFIT_STEP : TRAIL_FLAT_STEP;
      int gapPts  = profitable ? TRAIL_PROFIT_GAP  : TRAIL_FLAT_GAP;

      double candidate = RoundPriceUp(currentSL - stepPts * _Point);
      double gapLimit  = RoundPriceUp(ask + gapPts * _Point);
      double stopLimit = RoundPriceUp(ask + brokerGapPts * _Point);
      double minAllowed = MathMax(gapLimit, stopLimit);

      if(candidate < currentSL && candidate >= minAllowed - (_Point * 1e-6))
      {
         bool methodOK = trade.PositionModify(ticket, candidate, currentTP);
         CheckTradeResult(methodOK, "modify SELL SL");
      }
   }
}

//+------------------------------------------------------------------+
//| Trading window (broker server time). Supports overnight windows.  |
//+------------------------------------------------------------------+
bool dTime()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int hour = dt.hour;

   if(InpStartHour == InpStopHour)
      return true; // equal hours means 24-hour trading
   if(InpStartHour < InpStopHour)
      return (hour >= InpStartHour && hour < InpStopHour);
   return (hour >= InpStartHour || hour < InpStopHour);
}

//+------------------------------------------------------------------+
//| Build a validated BUY STOP price, SL and TP.                     |
//+------------------------------------------------------------------+
bool CalcBuyStop(double &price, double &sl, double &tp)
{
   double ask = SymbolAsk();
   if(ask <= 0.0)
      return false;

   int offsetPts = EntryOffsetPoints();
   price = RoundPriceUp(ask + offsetPts * _Point);

   // Keep the entry beyond current Ask by the broker's minimum distance.
   if(stopsLevelPts > 0 && price - ask < stopsLevelPts * _Point)
      price = RoundPriceUp(ask + stopsLevelPts * _Point);

   sl = 0.0;
   tp = 0.0;

   int slPts = EffectiveDistancePoints(InpStopLoss);
   int tpPts = EffectiveDistancePoints(InpTakeProfit);
   if(slPts > 0)
      sl = RoundPriceDown(price - slPts * _Point);
   if(tpPts > 0)
      tp = RoundPriceUp(price + tpPts * _Point);

   if(price <= ask)
      return false;
   if(sl > 0.0 && (price - sl) + 1e-9 < stopsLevelPts * _Point)
      return false;
   if(tp > 0.0 && (tp - price) + 1e-9 < stopsLevelPts * _Point)
      return false;
   return true;
}

//+------------------------------------------------------------------+
//| Build a validated SELL STOP price, SL and TP.                    |
//+------------------------------------------------------------------+
bool CalcSellStop(double &price, double &sl, double &tp)
{
   double bid = SymbolBid();
   if(bid <= 0.0)
      return false;

   int offsetPts = EntryOffsetPoints();
   price = RoundPriceDown(bid - offsetPts * _Point);

   if(stopsLevelPts > 0 && bid - price < stopsLevelPts * _Point)
      price = RoundPriceDown(bid - stopsLevelPts * _Point);

   sl = 0.0;
   tp = 0.0;

   int slPts = EffectiveDistancePoints(InpStopLoss);
   int tpPts = EffectiveDistancePoints(InpTakeProfit);
   if(slPts > 0)
      sl = RoundPriceUp(price + slPts * _Point);
   if(tpPts > 0)
      tp = RoundPriceDown(price - tpPts * _Point);

   if(price >= bid)
      return false;
   if(sl > 0.0 && (sl - price) + 1e-9 < stopsLevelPts * _Point)
      return false;
   if(tp > 0.0 && (price - tp) + 1e-9 < stopsLevelPts * _Point)
      return false;
   return true;
}

//+------------------------------------------------------------------+
//| Number of volume decimals implied by broker volume step.          |
//+------------------------------------------------------------------+
int CalcLotDigits()
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0)
      return 2;

   int digits = 0;
   while(digits < 8 && MathAbs(step - MathRound(step)) > 1e-10)
   {
      step *= 10.0;
      digits++;
   }
   return digits;
}

//+------------------------------------------------------------------+
//| Lot sizing using estimated monetary loss from entry to SL.        |
//| If calculated volume is below broker minimum, skip the trade      |
//| rather than force a larger position and exceed the risk setting.  |
//+------------------------------------------------------------------+
double CalcLotsForOrder(const ENUM_ORDER_TYPE orderType,
                        const double entryPrice,
                        const double stopLossPrice)
{
   double requestedLots = InpFixLot;

   if(InpAutoLot)
   {
      if(stopLossPrice <= 0.0)
      {
         LotWarning("AutoLot needs a valid StopLoss; order skipped.");
         return 0.0;
      }

      double estimatedProfit = 0.0;
      if(!OrderCalcProfit(orderType, _Symbol, 1.0, entryPrice,
                          stopLossPrice, estimatedProfit))
      {
         LotWarning("OrderCalcProfit failed; order skipped.");
         return 0.0;
      }
      if(estimatedProfit >= 0.0)
      {
         LotWarning("SL calculation did not produce an estimated loss; order skipped.");
         return 0.0;
      }

      double lossPerLot = -estimatedProfit;
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      double riskMoney = equity * InpRiskPercent / 100.0;
      if(lossPerLot <= 0.0 || riskMoney <= 0.0)
      {
         LotWarning("Invalid risk amount or loss-per-lot; order skipped.");
         return 0.0;
      }
      requestedLots = riskMoney / lossPerLot;
   }

   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(minLot <= 0.0 || maxLot < minLot || lotStep <= 0.0)
   {
      LotWarning("Invalid broker volume settings; order skipped.");
      return 0.0;
   }

   if(requestedLots < minLot - 1e-10)
   {
      LotWarning("Calculated/requested lot is below broker minimum; order skipped to avoid exceeding the risk limit.");
      return 0.0;
   }

   // Capping at maximum volume can only reduce risk versus the requested size.
   requestedLots = MathMin(requestedLots, maxLot);
   double lots = MathFloor(requestedLots / lotStep + 1e-10) * lotStep;
   lots = NormalizeDouble(lots, LotDigits);

   if(lots < minLot - 1e-10)
   {
      LotWarning("Volume-step normalization fell below broker minimum; order skipped.");
      return 0.0;
   }
   return lots;
}

//+------------------------------------------------------------------+
//| On-chart information panel                                       |
//+------------------------------------------------------------------+
void Display_Info()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int sec = dt.sec;

   int color1;
   if(sec < 10)      color1 = 8388608;
   else if(sec < 20) color1 = 0;
   else if(sec < 30) color1 = 2139610;
   else if(sec < 40) color1 = 25600;
   else if(sec < 50) color1 = 2970272;
   else              color1 = 8519755;

   int color2;
   if(sec < 10)      color2 = 8519755;
   else if(sec < 20) color2 = 16119285;
   else if(sec < 30) color2 = 25600;
   else if(sec < 40) color2 = 2970272;
   else if(sec < 50) color2 = 2139610;
   else              color2 = 8388608;

   string account = AccountInfoString(ACCOUNT_NAME);
   string broker  = AccountInfoString(ACCOUNT_SERVER);

   MakeLabel("AS_L01", "Webdings", 64, 2, 40, clrGold,
             CORNER_LEFT_UPPER, ANCHOR_LEFT_UPPER, "gg");
   MakeLabel("AS_L02", "Webdings", 63, 2, 41, (color)0,
             CORNER_LEFT_UPPER, ANCHOR_LEFT_UPPER, "gg");
   MakeLabel("AS_L03", "Webdings", 21, 2, 40, clrWhiteSmoke,
             CORNER_LEFT_UPPER, ANCHOR_LEFT_UPPER, "gggggg");
   MakeLabel("AS_L04", "Tahoma", 15, 23, 41, clrMintCream,
             CORNER_LEFT_UPPER, ANCHOR_LEFT_UPPER, "ALGO SAMURAI");
   MakeLabel("AS_L05", "Tahoma", 15, 22, 40, (color)color1,
             CORNER_LEFT_UPPER, ANCHOR_LEFT_UPPER, "ALGO SAMURAI");
   MakeLabel("AS_L06", "Arial", 10, 10, 75, clrWhiteSmoke,
             CORNER_LEFT_UPPER, ANCHOR_LEFT_UPPER, "Name :: " + account);
   MakeLabel("AS_L09", "Arial", 10, 10, 90, clrWhiteSmoke,
             CORNER_LEFT_UPPER, ANCHOR_LEFT_UPPER, "Broker :: " + broker);
   MakeLabel("AS_L10", "Arial", 10, 10, 105, (color)color1,
             CORNER_LEFT_UPPER, ANCHOR_LEFT_UPPER, "AlgoSamurai (MT5 port)");
   MakeLabel("AS_L11", "Arial", 10, 11, 106, clrLightGray,
             CORNER_LEFT_UPPER, ANCHOR_LEFT_UPPER, "AlgoSamurai (MT5 port)");
   MakeLabel("AS_L13", "Arial", 10, 21, 9, (color)0,
             CORNER_RIGHT_LOWER, ANCHOR_RIGHT_LOWER, "AlgoSamurai (MT5 port)");
   MakeLabel("AS_L14", "Arial", 10, 20, 8, clrMintCream,
             CORNER_RIGHT_LOWER, ANCHOR_RIGHT_LOWER, "AlgoSamurai (MT5 port)");
   MakeLabel("AS_L15", "Tahoma", 15, 21, 26, clrGold,
             CORNER_RIGHT_LOWER, ANCHOR_RIGHT_LOWER, "ALGO SAMURAI");
   MakeLabel("AS_L16", "Tahoma", 15, 20, 25, (color)color2,
             CORNER_RIGHT_LOWER, ANCHOR_RIGHT_LOWER, "ALGO SAMURAI");
}

//+------------------------------------------------------------------+
//| Create/update a chart label                                      |
//+------------------------------------------------------------------+
void MakeLabel(const string name,
               const string font,
               const int size,
               const int x,
               const int y,
               const color clr,
               const ENUM_BASE_CORNER corner,
               const ENUM_ANCHOR_POINT anchor,
               const string text)
{
   if(ObjectFind(0, name) < 0)
   {
      if(!ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0.0))
      {
         Print("AlgoSamurai: failed to create chart label ", name);
         return;
      }
   }

   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, font);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, size);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_CORNER, corner);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, anchor);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
}
//+------------------------------------------------------------------+
