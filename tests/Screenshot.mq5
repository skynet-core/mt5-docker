// Run on a live chart; ChartScreenShot is unavailable in Strategy Tester.
void OnStart()
{
   ChartRedraw();
   Sleep(1000);
   if(!ChartScreenShot(0, "validation-chart.png", 1600, 900, ALIGN_RIGHT))
      Print("Chart screenshot failed: ", GetLastError());
}
