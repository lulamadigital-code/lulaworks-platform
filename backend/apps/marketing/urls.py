from django.urls import path

from . import views

app_name = "marketing"

urlpatterns = [
    path("", views.home, name="home"),
    path("features/", views.features, name="features"),
    path("pricing/", views.pricing, name="pricing"),
    path("about/", views.about, name="about"),
    path("contact/", views.contact, name="contact"),
    path("demo/", views.demo, name="demo"),
    path("demo/thank-you/", views.demo_thanks, name="demo_thanks"),
    path("faq/", views.faq, name="faq"),
    path("learn/", views.learn, name="learn"),
    path("learn/path/<slug:slug>/", views.learn_path, name="learn_path"),
    path("learn/<slug:slug>/", views.learn_resource, name="learn_resource"),
    path("tools/", views.tools, name="tools"),
    path("tools/<slug:slug>/", views.tool, name="tool"),
    path("start-free-trial/", views.trial, name="trial"),
    path("privacy/", views.privacy, name="privacy"),
    path("terms/", views.terms, name="terms"),
    path("cookies/", views.cookies, name="cookies"),
]
