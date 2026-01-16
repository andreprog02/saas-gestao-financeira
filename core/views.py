from django.shortcuts import render

def dashboard(request):
    return render(request, "core/dashboard.html")


# Create your views here.
